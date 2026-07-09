"""
era5_district_clipper.py
========================
Orchestrates the end-to-end district-level ERA5 raster clipping
pipeline for the HydroAtlas backend.

This is the integration seam between the validated two-stage clipping
algorithm (lifted verbatim from ``raster-dist`` into
``backend/district_clip/``) and the production HydroAtlas data path:

    [POST /districts/{id}/raster-clip]
        │
        ▼
    Era5DistrictClipper.clip(gid, year, month, variable, padding_deg)
        │
        │  Stage 0 — Resolve the climate asset
        │  Existing PostgreSQL ``climate_assets`` row -> ClimateAsset
        ▼
    Repository.get_by_period(year, month, provider, variable)
        │
        │  Stage 1 — Acquire the local cached NetCDF
        │  Real S3 download via StoragePort, mediated by RasterCache
        │  (S3 key, checksum and download timing are recorded for
        │  operator visibility; cache hits skip S3 entirely).
        ▼
    RasterCache.acquire(asset, storage) -> RasterLease (with .path)
        │
        │  Stage 2 — Bbox-first I/O
        │  Computes a padded district bbox in EPSG:4326 / -180+180 lon
        │  space, then reads ONLY that window from the NetCDF. Handles
        │  ERA5's 0-360 lon convention + scale/offset/fill_value.
        ▼
    district_clip.netcdf_reader.read_netcdf_as_array(...)
        │
        │  Stage 3 — Exact geometric clip
        │  Per-cell intersection with the GADM district polygon in an
        │  LAEA equal-area projection centred on the district
        │  centroid. Boundary cells return partial polygon geometries;
        │  interior cells return full cells. Original ERA5 values
        │  preserved on every retained cell.
        ▼
    district_clip.raster_clip.mask_window_with_fractional_geometry(...)
        │
        │  Stage 4 — Pack into GeoJSON + summary + diagnostics
        ▼
    DistrictClipResult

This module deliberately does NOT bypass the existing S3/MinIO storage
port, the existing PostgreSQL asset index, or the existing bounded LRU
disk cache. Doing so would duplicate the production data path and
silently desynchronise the raster data behind the API from the data
behind the rest of HydroAtlas. The fact that ``RasterCache.acquire``
is the single funnel for S3 access is exactly what the existing
concurrent-open tests and precompute pipeline rely on.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
from shapely.geometry import box, mapping
from shapely.geometry.base import BaseGeometry

from application.raster_cache import RasterCache, RasterLease
from domain.entities.climate_asset import ClimateAsset
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort

from .gadm_lookup import DistrictMetadata, DistrictNotFoundError, lookup_district
from .netcdf_reader import read_netcdf_as_array
from .raster_clip import mask_window_with_fractional_geometry

logger = logging.getLogger("uvicorn.error")


# HydroAtlas public variable names -> ERA5 NetCDF variable names.
# Mirrors ``application.raster_computation.VARIABLE_MAP`` so the two
# clipping paths (stats + per-cell) agree on what the wire field means.
VARIABLE_MAP: Dict[str, str] = {
    "precipitation": "tp",
    "soil_moisture": "swvl1",
    "surface_runoff": "sro",
}


# Maximum number of GeoJSON features the endpoint will emit.  A single
# 0.1° ERA5 cell over a compact district (Davanagere) yields ~130 bbox
# cells; large maritime districts can yield 2-3x that.  10_000 leaves
# headroom for the largest Indian districts while still bounding the
# response payload so a runaway request cannot OOM the process.
DEFAULT_MAX_FEATURES = 10_000


@dataclass
class DistrictClipResult:
    """End-to-end result of a single district raster clip.

    Attributes
    ----------
    district_metadata : DistrictMetadata
        Names and GIDs for the requested district + parent state.
    variable : str
        HydroAtlas variable name (e.g. ``"precipitation"``).
    variable_long_name : str
        ERA5 ``long_name`` attribute for the variable.
    nc_variable : str
        ERA5 NetCDF variable name (e.g. ``"tp"``).
    units : str
        ERA5 ``units`` attribute for the variable.
    year, month : int
        Period the NetCDF represents.
    time_decoded : Optional[str]
        ISO timestamp of the NetCDF ``valid_time`` value, if available.
    bbox_used : Tuple[float, float, float, float]
        ``(minx, miny, maxx, maxy)`` in EPSG:4326 (-180/+180 lon
        convention) of the padded bounding box used for the Stage-2
        NetCDF read.
    source_resolution_deg : float
        ERA5 grid resolution in degrees (always 0.1 for ERA5-Land).
    asset_id : str
        ``climate_assets.id`` row used for this clip.
    asset_storage_key : str
        S3 key (or local equivalent) of the NetCDF used for this clip.
        Operators can grep server logs on this exact string.
    cache_hit : bool
        ``True`` if the NetCDF was already in the on-disk cache and
        no S3 download was performed.
    feature_collection : Dict[str, Any]
        A valid GeoJSON ``FeatureCollection`` describing every retained
        cell. See :meth:`Era5DistrictClipper.clip` for the feature
        property schema.
    summary : Dict[str, Any]
        Summary statistics (mean/min/max/std/sum/median/p25/p75) plus
        the boundary-cell / total-cell / excluded-cell counts.
    diagnostics : Dict[str, Any]
        Wall-time + I/O + payload-size measurements for one clip.
    """

    district_metadata: DistrictMetadata
    variable: str
    variable_long_name: str
    nc_variable: str
    units: str
    year: int
    month: int
    time_decoded: Optional[str]
    bbox_used: Tuple[float, float, float, float]
    source_resolution_deg: float
    asset_id: str
    asset_storage_key: str
    cache_hit: bool
    feature_collection: Dict[str, Any] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


class Era5DistrictClipper:
    """Stateless orchestrator for a single district clip.

    The class carries only references to the production collaborators
    (``repository``, ``storage``, ``raster_cache``); all per-clip
    state lives in :meth:`clip`'s local scope and the returned
    :class:`DistrictClipResult`. The class is therefore cheap to
    instantiate per-request — there is no cached mutable state.

    Parameters
    ----------
    repository :
        Existing :class:`domain.ports.dataset_repository.DatasetRepository`
        implementation. Used to resolve the ``ClimateAsset`` for the
        requested ``(provider, variable, year, month)``.
    storage :
        Existing :class:`domain.ports.storage_port.StoragePort`
        implementation (S3 in production; S3-compatible in tests).
        Forwarded to :class:`RasterCache` so the NetCDF reaches the
        on-disk cache exactly the way every other HydroAtlas endpoint
        fetches it.
    raster_cache :
        Existing :class:`application.raster_cache.RasterCache`. When
        ``None``, a fresh cache is created using the module-level
        default (matches the behaviour of ``RasterComputation``).
    max_features :
        Hard cap on the number of GeoJSON features the endpoint will
        emit. See :data:`DEFAULT_MAX_FEATURES`.
    """

    def __init__(
        self,
        repository: DatasetRepository,
        storage: StoragePort,
        raster_cache: Optional[RasterCache] = None,
        max_features: int = DEFAULT_MAX_FEATURES,
    ) -> None:
        self.repository = repository
        self.storage = storage
        # Reuse the singleton cache the same way RasterComputation does
        # so cache hits are shared between the stats endpoints and the
        # new per-cell endpoint.
        self._raster_cache = raster_cache or RasterCache()
        self.max_features = max_features

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def clip(
        self,
        district_id: str,
        year: int,
        month: int,
        variable: str,
        padding_deg: float = 0.1,
        provider: str = "era5-land",
    ) -> DistrictClipResult:
        """Execute the full two-stage clip and return GeoJSON + stats.

        Parameters
        ----------
        district_id :
            GADM ``GID_2`` (e.g. ``"IND.16.13_1"`` for Davanagere).
        year, month :
            Period the NetCDF represents. The fundamental unit is one
            month, mirroring the rest of HydroAtlas.
        variable :
            HydroAtlas variable name; one of
            ``"precipitation"``, ``"soil_moisture"``, ``"surface_runoff"``.
        padding_deg :
            Padding in degrees added to the district bbox before the
            Stage-2 NetCDF read. Defaults to ``0.1`` (one ERA5 cell)
            which exactly matches the prototype's validated setting.
        provider :
            Climate data provider key; defaults to ``"era5-land"``.

        Returns
        -------
        DistrictClipResult

        Raises
        ------
        DistrictNotFoundError
            ``district_id`` is not in the GADM ADM_2 layer.
        KeyError
            ``variable`` is not a known HydroAtlas variable.
        ValueError
            The PostgreSQL ``climate_assets`` table has no row for the
            requested ``(provider, variable, year, month)`` combination,
            or the district bbox does not intersect the NetCDF grid.
        FileNotFoundError
            The NetCDF was resolved through the asset table but the
            underlying file is not on disk and S3 download failed.
        """
        if variable not in VARIABLE_MAP:
            raise KeyError(
                f"Unknown variable '{variable}'. "
                f"Valid options: {sorted(VARIABLE_MAP.keys())}"
            )
        nc_variable = VARIABLE_MAP[variable]

        t_total_start = time.perf_counter()

        # Stage 0 — resolve the district geometry + metadata
        t_lookup_start = time.perf_counter()
        geometry, metadata = lookup_district(district_id)
        t_lookup_seconds = time.perf_counter() - t_lookup_start
        logger.info(
            "DISTRICT_CLIP district=%s (%s / %s) geom_type=%s",
            metadata.gid_2, metadata.name_2, metadata.name_1,
            geometry.geom_type,
        )

        # Stage 0b — resolve the climate asset (real S3-backed data path)
        t_asset_start = time.perf_counter()
        asset = await self.repository.get_by_period(
            year=year, month=month, provider=provider, variable=variable,
        )
        t_asset_seconds = time.perf_counter() - t_asset_start
        if asset is None:
            raise ValueError(
                f"No climate_assets row for {provider}/{variable}/"
                f"{year:04d}-{month:02d}; ingest this period first."
            )
        logger.info(
            "DISTRICT_CLIP asset_resolved id=%s storage_key=%s "
            "checksum=%s size=%s",
            asset.id, asset.storage_key, asset.checksum, asset.file_size,
        )

        # Stages 1-4 — acquire the cached NetCDF, bbox-read, fractional
        # clip, and pack the result. The lease MUST be released in a
        # finally block so the on-disk cache file remains eligible for
        # the eviction sweep once the clip is done.
        lease: Optional[RasterLease] = None
        try:
            t_acquire_start = time.perf_counter()
            lease = await self._raster_cache.acquire(asset, self.storage)
            t_acquire_seconds = time.perf_counter() - t_acquire_start
            logger.info(
                "DISTRICT_CLIP cache_path=%s cache_hit=%s "
                "bytes_downloaded=%d source=%s wait=%.3fs "
                "download=%.3fs validate=%.3fs",
                lease.path, lease.cache_hit, lease.bytes_downloaded,
                lease.source, lease.wait_seconds,
                lease.download_seconds, lease.validate_seconds,
            )

            result = self._clip_from_local_path(
                nc_path=lease.path,
                geometry=geometry,
                metadata=metadata,
                nc_variable=nc_variable,
                variable=variable,
                year=year,
                month=month,
                padding_deg=padding_deg,
                asset_id=asset.id,
                asset_storage_key=asset.storage_key,
                cache_hit=lease.cache_hit,
            )

            t_total_seconds = time.perf_counter() - t_total_start
            result.diagnostics.update({
                "district_lookup_seconds": round(t_lookup_seconds, 4),
                "asset_lookup_seconds": round(t_asset_seconds, 4),
                "cache_acquire_seconds": round(t_acquire_seconds, 4),
                "s3_download_seconds": round(lease.download_seconds, 4),
                "request_duration_seconds": round(t_total_seconds, 4),
            })
            logger.info(
                "DISTRICT_CLIP done district=%s variable=%s "
                "valid_cells=%d boundary_cells=%d "
                "request_duration=%.3fs",
                metadata.gid_2, variable,
                result.summary.get("valid_cells", 0),
                result.summary.get("boundary_cells", 0),
                t_total_seconds,
            )
            return result
        finally:
            if lease is not None:
                try:
                    lease.release()
                except Exception:  # noqa: BLE001
                    # Lease release is idempotent; the only failure mode
                    # here is a registry bookkeeping error that the
                    # next request will surface anyway.
                    pass

    # ------------------------------------------------------------------
    # Sync core — runs after the NetCDF is local
    # ------------------------------------------------------------------

    def _clip_from_local_path(
        self,
        *,
        nc_path: Path,
        geometry: BaseGeometry,
        metadata: DistrictMetadata,
        nc_variable: str,
        variable: str,
        year: int,
        month: int,
        padding_deg: float,
        asset_id: str,
        asset_storage_key: str,
        cache_hit: bool,
    ) -> DistrictClipResult:
        """Execute stages 2-4 of the pipeline against a local NetCDF.

        Kept synchronous so the algorithmic core is identical to the
        standalone prototype; the only async work is asset resolution
        and cache acquisition, both of which happen in :meth:`clip`.
        """
        t_core_start = time.perf_counter()

        # Stage 2 — bbox-first I/O using the validated prototype reader.
        bbox = _padded_bbox(geometry, padding=padding_deg)
        t_bbox_read_start = time.perf_counter()
        arr_2d, affine, crs, fill_value, nc_meta = read_netcdf_as_array(
            path=nc_path,
            variable=nc_variable,
            time_index=0,            # monthly aggregate -> one time slice
            bbox=bbox,
        )
        t_bbox_read_seconds = time.perf_counter() - t_bbox_read_start
        n_lat, n_lon = arr_2d.shape
        bbox_cells_loaded = int(arr_2d.size)
        logger.info(
            "DISTRICT_CLIP bbox_read shape=%dx%d (cells=%d) "
            "time=%s transform=%s",
            n_lat, n_lon, bbox_cells_loaded,
            nc_meta.get("time_decoded"), affine,
        )

        # Stage 3 — exact geometric clip with per-cell intersection
        # geometries. NaN -> treated as nodata by the prototype code.
        t_clip_start = time.perf_counter()
        masked, overlaps, cell_geoms = mask_window_with_fractional_geometry(
            window_array=arr_2d.astype(np.float32, copy=False),
            window_transform=affine,
            geometry=geometry,
            raster_crs="EPSG:4326",
            nodata=None,
            all_touched=False,
        )
        # NaN must remain masked regardless of overlap.
        nan_mask = np.isnan(arr_2d)
        masked = np.ma.array(
            masked.data,
            mask=(np.asarray(masked.mask) | nan_mask),
            fill_value=masked.fill_value,
        )
        t_clip_seconds = time.perf_counter() - t_clip_start

        # Stage 4 — pack the masked result into a GeoJSON
        # FeatureCollection plus summary stats and diagnostics.
        t_pack_start = time.perf_counter()
        feature_collection, summary = _build_geojson_and_summary(
            masked_array=masked,
            overlaps=overlaps,
            cell_geometries=cell_geoms,
            affine=affine,
            variable=variable,
            nc_variable=nc_variable,
            bbox_cells_loaded=bbox_cells_loaded,
        )
        t_pack_seconds = time.perf_counter() - t_pack_start

        # Diagnostics are filled incrementally by the async wrapper too;
        # here we add the per-stage timings and feature count.
        serialized_bytes = len(
            json.dumps(feature_collection, separators=(",", ":")).encode("utf-8")
        )
        t_core_seconds = time.perf_counter() - t_core_start
        diagnostics = {
            "bbox_cells_loaded": bbox_cells_loaded,
            "cells_retained": int(summary["valid_cells"]),
            "cells_excluded": int(summary.get("excluded_cells", 0)),
            "bbox_read_seconds": round(t_bbox_read_seconds, 4),
            "clip_seconds": round(t_clip_seconds, 4),
            "pack_seconds": round(t_pack_seconds, 4),
            "serialized_response_bytes": int(serialized_bytes),
            "engine": "raster_dist+netcdf4+laea_equal_area",
            "nc_path": str(nc_path),
        }

        return DistrictClipResult(
            district_metadata=metadata,
            variable=variable,
            variable_long_name=str(nc_meta.get("long_name", variable)),
            nc_variable=nc_variable,
            units=str(nc_meta.get("units", "unknown")),
            year=year,
            month=month,
            time_decoded=nc_meta.get("time_decoded"),
            bbox_used=tuple(bbox),
            source_resolution_deg=float(abs(nc_meta.get("lat_step", 0.1))),
            asset_id=asset_id,
            asset_storage_key=asset_storage_key,
            cache_hit=cache_hit,
            feature_collection=feature_collection,
            summary=summary,
            diagnostics=diagnostics,
        )


# ---------------------------------------------------------------------------
# Helpers (module-level so they are independently testable)
# ---------------------------------------------------------------------------

def _padded_bbox(
    geometry: BaseGeometry,
    padding: float,
) -> Tuple[float, float, float, float]:
    """Return ``(minx, miny, maxx, maxy)`` in EPSG:4326 / -180/+180 lon.

    Identical in semantics to ``raster_clip.get_padded_bbox`` from the
    prototype but inlined here so this module does not import the
    whole ``raster_clip`` module just for one helper.
    """
    if geometry.is_empty:
        raise ValueError("Cannot compute bbox of an empty geometry.")
    minx, miny, maxx, maxy = geometry.bounds
    return (minx - padding, miny - padding, maxx + padding, maxy + padding)


def _build_geojson_and_summary(
    *,
    masked_array: np.ma.MaskedArray,
    overlaps: np.ndarray,
    cell_geometries: np.ndarray,
    affine: Any,
    variable: str,
    nc_variable: str,
    bbox_cells_loaded: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Convert the masked raster + per-cell geoms into GeoJSON + stats.

    Each retained cell becomes a GeoJSON ``Feature`` with the following
    properties:

        value             — original ERA5 cell value (preserved)
        variable          — HydroAtlas public variable name
        nc_variable       — ERA5 NetCDF variable name
        units             — ERA5 units attribute (filled at response level)
        row, col          — source-grid indices (debug aid)
        center_lon, center_lat — source-grid cell centre
        is_boundary_cell  — True iff the cell was cut by the district border
        overlap_fraction  — [0,1] area of the cell inside the district

    Boundary cells preserve the original ERA5 value; only the geometry
    is clipped. Cells entirely outside the district are excluded. The
    returned ``summary`` mirrors the prototype's :func:`stats.compute_stats`
    output plus boundary / excluded cell counts.
    """
    n_rows, n_cols = masked_array.shape
    mask = np.asarray(masked_array.mask)
    data = np.asarray(masked_array.data)

    features: list[Dict[str, Any]] = []
    values: list[float] = []
    boundary_cells = 0
    excluded_cells = 0
    partial_geom_count = 0

    # Affine descriptor — ``c`` is the left edge of column 0, ``f`` is
    # the top edge of row 0. ``a`` is the x-pixel size (+), ``e`` is
    # the y-pixel size (− for north-up rasters).
    a = float(affine.a)
    e = float(affine.e)
    c0 = float(affine.c)
    f0 = float(affine.f)

    # Cell-edge step sizes (positive numbers)
    dx = abs(a)
    dy = abs(e)

    for r in range(n_rows):
        for col in range(n_cols):
            if mask[r, col]:
                excluded_cells += 1
                continue
            value = float(data[r, col])
            values.append(value)
            overlap = float(overlaps[r, col])
            cell_geom = cell_geometries[r, col]

            # Build the per-feature polygon.
            if cell_geom is None or cell_geom.is_empty:
                # Fully outside — should already be masked, but guard.
                excluded_cells += 1
                continue
            is_boundary = bool(overlap < 0.999999)
            if is_boundary:
                boundary_cells += 1
                partial_geom_count += 1

            geo = mapping(cell_geom)

            # Source-grid indices + cell centre for debug display.
            lon_center = c0 + (col + 0.5) * a
            lat_center = f0 + (r + 0.5) * e

            features.append({
                "type": "Feature",
                "properties": {
                    "value": value,
                    "variable": variable,
                    "nc_variable": nc_variable,
                    "row": int(r),
                    "col": int(col),
                    "center_lon": round(lon_center, 6),
                    "center_lat": round(lat_center, 6),
                    "is_boundary_cell": is_boundary,
                    "overlap_fraction": round(overlap, 6),
                },
                "geometry": geo,
            })

    feature_collection: Dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
    }

    # Summary stats. NaN-aware reduction over the valid-cell values.
    n_valid = len(values)
    if n_valid > 0:
        v = np.asarray(values, dtype=np.float64)
        mean = float(np.mean(v))
        std = float(np.std(v))
        vmin = float(np.min(v))
        vmax = float(np.max(v))
        vsum = float(np.sum(v))
        vmed = float(np.median(v))
        p25 = float(np.percentile(v, 25))
        p75 = float(np.percentile(v, 75))
    else:
        mean = std = vmin = vmax = vsum = vmed = p25 = p75 = float("nan")

    summary: Dict[str, Any] = {
        "valid_cells": int(n_valid),
        "boundary_cells": int(boundary_cells),
        "excluded_cells": int(excluded_cells),
        "bbox_cells_total": int(bbox_cells_loaded),
        "mean": mean,
        "std": std,
        "min": vmin,
        "max": vmax,
        "sum": vsum,
        "median": vmed,
        "p25": p25,
        "p75": p75,
        "partial_geom_count": int(partial_geom_count),
    }
    return feature_collection, summary
