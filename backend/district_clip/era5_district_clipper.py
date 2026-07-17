from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

# Maps HydroAtlas variable names to ERA5 NetCDF variable names.
VARIABLE_MAP: Dict[str, str] = {
    "precipitation": "tp",
    "soil_moisture": "swvl1",
    "surface_runoff": "sro",
}



DEFAULT_MAX_FEATURES = 10_000


@dataclass
class DistrictClipResult:


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
   
    def __init__(
        self,
        repository: DatasetRepository,
        storage: StoragePort,
        raster_cache: Optional[RasterCache] = None,
        max_features: int = DEFAULT_MAX_FEATURES,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self._raster_cache = raster_cache or RasterCache()
        self.max_features = max_features


    # Public API
    

    async def clip(
        self,
        district_id: str,
        year: int,
        month: int,
        variable: str,
        padding_deg: float = 0.1,
        provider: str = "era5-land",
    ) -> DistrictClipResult:
        
        if variable not in VARIABLE_MAP:
            raise KeyError(
                f"Unknown variable '{variable}'. "
                f"Valid options: {sorted(VARIABLE_MAP.keys())}"
            )
        nc_variable = VARIABLE_MAP[variable]

        t_total_start = time.perf_counter()

      
        t_lookup_start = time.perf_counter()
        geometry, metadata = lookup_district(district_id)
        t_lookup_seconds = time.perf_counter() - t_lookup_start
        logger.info(
            "DISTRICT_CLIP district=%s (%s / %s) geom_type=%s",
            metadata.gid_2, metadata.name_2, metadata.name_1,
            geometry.geom_type,
        )

     
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
                    
                    pass

    async def clip_range(
        self,
        district_id: str,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
        variable: str,
        padding_deg: float = 0.1,
        provider: str = "era5-land",
    ) -> DistrictClipResult:
        """Clip and average raster cells across a month range for a district."""
        if variable not in VARIABLE_MAP:
            raise KeyError(
                f"Unknown variable '{variable}'. "
                f"Valid options: {sorted(VARIABLE_MAP.keys())}"
            )
        nc_variable = VARIABLE_MAP[variable]

        t_total_start = time.perf_counter()

        # --- Resolve district geometry ---
        t_lookup_start = time.perf_counter()
        geometry, metadata = lookup_district(district_id)
        t_lookup_seconds = time.perf_counter() - t_lookup_start
        logger.info(
            "DISTRICT_CLIP_RANGE district=%s (%s / %s) geom_type=%s "
            "range=%04d-%02d..%04d-%02d",
            metadata.gid_2, metadata.name_2, metadata.name_1,
            geometry.geom_type,
            start_year, start_month, end_year, end_month,
        )

        # Padded bbox is constant across months.
        bbox = _padded_bbox(geometry, padding=padding_deg)

        # --- Fetch all assets for the range ---
        t_asset_start = time.perf_counter()
        assets = await self.repository.list_by_period_range(
            start_year=start_year,
            start_month=start_month,
            end_year=end_year,
            end_month=end_month,
            provider=provider,
            variable=variable,
        )
        t_asset_seconds = time.perf_counter() - t_asset_start
        if not assets:
            raise ValueError(
                f"No climate_assets rows for {provider}/{variable} "
                f"between {start_year:04d}-{start_month:02d} and "
                f"{end_year:04d}-{end_month:02d}; ingest this period first."
            )
        logger.info(
            "DISTRICT_CLIP_RANGE resolved %d assets for district=%s",
            len(assets), metadata.gid_2,
        )

        # --- Clip each month and accumulate raw 2-D arrays ---
        #
        # We stack all months' clipped arrays into a 3-D numpy array
        # (n_months, n_lat, n_lon) and then call nanmean along axis=0
        # to produce the per-pixel mean, ignoring NaN (nodata) cells.
        #
        # The affine transform, cell geometry arrays, and overlap fractions
        # are taken from the first month's clip — they are identical across
        # months because the underlying ERA5 grid is fixed.

        monthly_arrays: List[np.ndarray] = []
        ref_affine = None
        ref_overlaps = None
        ref_cell_geoms = None
        ref_nc_meta: Dict[str, Any] = {}
        first_asset_id = assets[0].id
        first_storage_key = assets[0].storage_key
        any_cache_hit = False
        months_processed = 0

        for asset in assets:
            lease: Optional[RasterLease] = None
            try:
                lease = await self._raster_cache.acquire(asset, self.storage)
                if lease.cache_hit:
                    any_cache_hit = True
                logger.info(
                    "DISTRICT_CLIP_RANGE month=%04d-%02d cache_path=%s "
                    "cache_hit=%s bytes_downloaded=%d",
                    asset.year, asset.month,
                    lease.path, lease.cache_hit, lease.bytes_downloaded,
                )

                arr_2d, affine, _crs, _fill, nc_meta = read_netcdf_as_array(
                    path=lease.path,
                    variable=nc_variable,
                    time_index=0,
                    bbox=bbox,
                )

                # Clip to district polygon — identical call to _clip_from_local_path.
                masked, overlaps, cell_geoms = mask_window_with_fractional_geometry(
                    window_array=arr_2d.astype(np.float32, copy=False),
                    window_transform=affine,
                    geometry=geometry,
                    raster_crs="EPSG:4326",
                    nodata=None,
                    all_touched=False,
                )
                # Propagate original NaN cells into the mask.
                nan_mask = np.isnan(arr_2d)
                full_mask = np.asarray(masked.mask) | nan_mask

                # Build a plain float array; keep NaN where masked so nanmean
                # ignores those pixels.
                clipped_data = np.where(full_mask, np.nan, np.asarray(masked.data, dtype=np.float64))

                monthly_arrays.append(clipped_data)

                # Capture reference geometry/transform from the first month.
                if ref_affine is None:
                    ref_affine = affine
                    ref_overlaps = overlaps
                    ref_cell_geoms = cell_geoms
                    ref_nc_meta = nc_meta
                    first_asset_id = asset.id
                    first_storage_key = asset.storage_key

                months_processed += 1
                logger.info(
                    "DISTRICT_CLIP_RANGE month=%04d-%02d clipped shape=%s",
                    asset.year, asset.month, clipped_data.shape,
                )
            finally:
                if lease is not None:
                    try:
                        lease.release()
                    except Exception:  # noqa: BLE001
                        pass

        if months_processed == 0 or ref_affine is None:
            raise ValueError(
                "No climate data could be clipped for the selected period."
            )

        # --- Per-pixel nanmean across all months ---
        stacked = np.stack(monthly_arrays, axis=0)  # (n_months, n_lat, n_lon)
        mean_array = np.nanmean(stacked, axis=0)    # (n_lat, n_lon)

        # Cells that are NaN in *every* month stay NaN (all-nodata pixel).
        all_nan_mask = np.all(np.isnan(stacked), axis=0)

        avg_masked = np.ma.array(
            mean_array,
            mask=all_nan_mask,
            fill_value=np.nan,
        )

        # --- Re-use existing GeoJSON builder with averaged array ---
        t_pack_start = time.perf_counter()
        bbox_cells_loaded = int(mean_array.size)
        feature_collection, summary = _build_geojson_and_summary(
            masked_array=avg_masked,
            overlaps=ref_overlaps,
            cell_geometries=ref_cell_geoms,
            affine=ref_affine,
            variable=variable,
            nc_variable=nc_variable,
            bbox_cells_loaded=bbox_cells_loaded,
        )
        t_pack_seconds = time.perf_counter() - t_pack_start

        t_total_seconds = time.perf_counter() - t_total_start
        serialized_bytes = len(
            json.dumps(feature_collection, separators=(",", ":")).encode("utf-8")
        )
        diagnostics = {
            "months_processed": months_processed,
            "bbox_cells_loaded": bbox_cells_loaded,
            "cells_retained": int(summary["valid_cells"]),
            "cells_excluded": int(summary.get("excluded_cells", 0)),
            "district_lookup_seconds": round(t_lookup_seconds, 4),
            "asset_lookup_seconds": round(t_asset_seconds, 4),
            "pack_seconds": round(t_pack_seconds, 4),
            "request_duration_seconds": round(t_total_seconds, 4),
            "serialized_response_bytes": int(serialized_bytes),
            "engine": "raster_dist+netcdf4+laea_equal_area+nanmean",
        }

        logger.info(
            "DISTRICT_CLIP_RANGE done district=%s variable=%s "
            "months_processed=%d valid_cells=%d "
            "request_duration=%.3fs",
            metadata.gid_2, variable,
            months_processed,
            summary.get("valid_cells", 0),
            t_total_seconds,
        )

        return DistrictClipResult(
            district_metadata=metadata,
            variable=variable,
            variable_long_name=str(ref_nc_meta.get("long_name", variable)),
            nc_variable=nc_variable,
            units=str(ref_nc_meta.get("units", "unknown")),
            # Report the start year/month so the frontend can label the range.
            year=start_year,
            month=start_month,
            time_decoded=ref_nc_meta.get("time_decoded"),
            bbox_used=tuple(bbox),
            source_resolution_deg=float(abs(ref_nc_meta.get("lat_step", 0.1))),
            asset_id=first_asset_id,
            asset_storage_key=first_storage_key,
            cache_hit=any_cache_hit,
            feature_collection=feature_collection,
            summary=summary,
            diagnostics=diagnostics,
        )


    # Sync core — runs after the NetCDF is local
    

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
        
        t_core_start = time.perf_counter()

        # Read the NetCDF window for the padded district bbox.
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

        # Clip each cell to the district boundary and treat NaN as nodata.
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

        # Build the GeoJSON response and summary stats.
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
    """Return ``(minx, miny, maxx, maxy)`` in epsg:4326 / -180/+180 lon."""
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
   
    n_rows, n_cols = masked_array.shape
    mask = np.asarray(masked_array.mask)
    data = np.asarray(masked_array.data)

    features: list[Dict[str, Any]] = []
    values: list[float] = []
    boundary_cells = 0
    excluded_cells = 0
    partial_geom_count = 0

  
    a = float(affine.a)
    e = float(affine.e)
    c0 = float(affine.c)
    f0 = float(affine.f)

    # Cell size in each direction.
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
