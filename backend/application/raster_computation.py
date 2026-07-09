"""Geospatial computation for ERA5 climate data."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import time
from typing import Iterable

import geopandas as gpd
import numpy as np
import rioxarray
import xarray as xr

from application.diagnostics import flush, get_request_id, safe_log
from application.dto.responses import MonthlySeriesPoint, StateDistrictStatisticsItem
from application.raster_cache import OpenRasterHandle, RasterCache, RasterLease
from domain.entities.climate_asset import ClimateAsset
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort
from infrastructure.geospatial.boundary_loader import get_adm2

logger = logging.getLogger("uvicorn.error")


# Mapping from HydroAtlas variable names to ERA5 NetCDF variable names
VARIABLE_MAP = {
    "precipitation": "tp",
    "soil_moisture": "swvl1",
    "surface_runoff": "sro",
}


@dataclass
class RasterClipResult:
    """Result of clipping a raster to a district geometry."""
    pixel_count: int
    valid_pixel_count: int
    valid_pixel_percentage: float
    bounds: tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)
    mean: float
    minimum: float
    maximum: float


@dataclass
class AggregatedRasterStats:
    """Aggregated statistics for a single geometry over a month range."""

    months_processed: int
    mean: float
    minimum: float
    maximum: float


def _close_and_cleanup(raster: xr.Dataset | xr.DataArray | None, lease: RasterLease | None) -> None:
    """Close the xarray object and release the raster cache lease.

    Kept as a transitional helper for callers that acquired the
    dataset and lease independently. New callers should use
    :class:`~application.raster_cache.OpenRasterHandle` instead and
    call ``handle.close()`` (or ``async with handle``) so the
    dataset/lease ownership is bundled.

    Called inside ``finally`` blocks so the caller never leaks file
    handles even when an exception is raised mid-iteration. The lease
    keeps the underlying file protected from eviction while the
    caller is using it; releasing the lease here makes the file
    eligible for eviction again.
    """
    if raster is not None:
        try:
            raster.close()
        except Exception:
            pass
    if lease is not None:
        try:
            lease.release()
        except Exception:
            pass


class RasterComputation:
    """Handles geospatial computations on ERA5 raster data."""

    def __init__(
        self,
        repository: DatasetRepository,
        storage: StoragePort,
        raster_cache: RasterCache | None = None,
    ):
        self.repository = repository
        self.storage = storage
        # Module-level lock and lease registries are singletons; we only
        # carry the cache root + budget here.
        self._raster_cache = raster_cache or RasterCache()

    async def _load_monthly_raster(
        self,
        provider: str,
        year: int,
        month: int,
        variable: str,
    ) -> OpenRasterHandle:
        t0 = time.perf_counter()
        asset = await self.repository.get_by_period(year, month, provider, variable)
        t1 = time.perf_counter()
        logger.info("PostgreSQL asset lookup: %.3fs", t1 - t0)
        if not asset:
            raise ValueError(f"No dataset found for {provider}/{year}/{month:02d}")
        return await self.read_raster_from_s3(asset)

    def _select_raster_variable(
        self,
        raster: xr.Dataset | xr.DataArray,
        variable: str,
    ) -> xr.DataArray:
        nc_var = VARIABLE_MAP.get(variable)
        if nc_var is None:
            raise ValueError(f"Unknown variable '{variable}'. Valid options: {list(VARIABLE_MAP.keys())}")

        if isinstance(raster, xr.DataArray):
            data = raster
            if data.name and data.name != nc_var:
                raise ValueError(
                    f"Variable '{nc_var}' not found in raster. Available: '{data.name}'"
                )
        else:
            if nc_var not in raster.data_vars:
                raise ValueError(f"Variable '{nc_var}' not found in NetCDF. Available: {list(raster.data_vars)}")
            data = raster[nc_var]

        if len(data.dims) == 3 and data.shape[0] == 1:
            data = data.isel({data.dims[0]: 0})

        return data

    def _compute_stats_for_geometry(
        self,
        data: xr.DataArray,
        geometry: gpd.GeoDataFrame,
    ) -> RasterClipResult:
        req_id = get_request_id()
        # Eager metadata conversion: use tuple(data.dims) for a
        # DataArray (data.dims is a tuple of dim-name strings; dict(...)
        # raises ValueError). Routed through safe_log so any future
        # formatting failure cannot interrupt the clip+compute path.
        safe_log(
            logging.INFO,
            "RIO_CLIP_BEGIN request_id=%s dims=%s shape=%s",
            req_id, tuple(data.dims), tuple(data.shape),
        )
        flush()
        clipped = data.rio.clip(geometry.geometry.values, geometry.crs)
        bounds = clipped.rio.bounds()
        logger.info(
            "RIO_CLIP_DONE request_id=%s bounds=%s clipped_shape=%s",
            req_id, bounds, tuple(clipped.shape),
        )
        flush()

        values = clipped.values
        total_pixels = values.size
        logger.info(
            "NUMPY_AGGREGATE_BEGIN request_id=%s total_pixels=%d",
            req_id, total_pixels,
        )
        flush()
        valid_mask = ~np.isnan(values)
        valid_pixel_count = int(valid_mask.sum())
        valid_pixel_percentage = (valid_pixel_count / total_pixels * 100) if total_pixels > 0 else 0.0

        valid_data = values[valid_mask]
        mean_value = float(np.mean(valid_data)) if valid_pixel_count > 0 else 0.0
        min_value = float(np.min(valid_data)) if valid_pixel_count > 0 else 0.0
        max_value = float(np.max(valid_data)) if valid_pixel_count > 0 else 0.0
        logger.info(
            "NUMPY_AGGREGATE_DONE request_id=%s valid_pixels=%d mean=%.6f min=%.6f max=%.6f",
            req_id, valid_pixel_count, mean_value, min_value, max_value,
        )
        flush()

        return RasterClipResult(
            pixel_count=int(total_pixels),
            valid_pixel_count=valid_pixel_count,
            valid_pixel_percentage=float(valid_pixel_percentage),
            bounds=bounds,
            mean=mean_value,
            minimum=min_value,
            maximum=max_value,
        )

    def get_district_geometry(self, district_gid: str) -> gpd.GeoDataFrame:
        """Load district geometry by GID_2 from GADM."""
        adm2 = get_adm2()
        district = adm2[adm2["GID_2"] == district_gid]
        if district.empty:
            raise ValueError(f"District not found: {district_gid}")
        return district

    async def read_raster_from_s3(self, asset: ClimateAsset) -> OpenRasterHandle:
        """Resolve ``asset`` through the shared raster cache and open it.

        The local cache file at
        ``Settings.raster_cache_root_resolved()/{provider}/{variable}/{YYYY}/{MM}.nc``
        is reused across requests; concurrent requests for the same asset
        coalesce into one S3 download via the cache's per-key single-flight,
        and opens are serialised per storage key via
        :meth:`application.raster_cache.RasterCache.open_dataset`.

        Returns an :class:`~application.raster_cache.OpenRasterHandle`
        bundling the opened dataset, cache path, and active
        :class:`RasterLease`. The caller MUST close the handle once it
        is done so the file handle is dropped and the cache file becomes
        eviction-eligible.

        Exception safety:
          * If ``raster_cache.acquire`` succeeds but ``open_dataset``
            raises, the lease is released in the ``except`` arm so the
            caller does not leak an eviction-protection refcount.
          * If the caller raises while using the dataset, the handle's
            ``close()`` (invoked from ``finally`` or via async-context-
            manager) releases both the dataset and the lease.
        """
        lease = await self._raster_cache.acquire(asset, self.storage)
        try:
            rds = await self._raster_cache.open_dataset(lease, asset=asset)
            logger.info(
                "Opened raster file: %s  vars=%s",
                lease.path, list(rds.data_vars),
            )
            rds = rds.rio.write_crs("EPSG:4326")
        except Exception:
            # open_dataset (or write_crs) failed — release the lease so
            # the cache file is not protected forever with no reader.
            try:
                lease.release()
            except Exception:
                pass
            raise
        return OpenRasterHandle(dataset=rds, path=lease.path, lease=lease)

    def clip_raster(
        self,
        raster: xr.Dataset | xr.DataArray,
        geometry: gpd.GeoDataFrame,
        variable: str,
    ) -> RasterClipResult:
        """Clip raster to district geometry and compute statistics for specified variable."""
        data = self._select_raster_variable(raster, variable)
        return self._compute_stats_for_geometry(data, geometry)

    async def _aggregate_for_geometry(
        self,
        assets: Iterable[ClimateAsset],
        geometry: gpd.GeoDataFrame,
        variable: str,
    ) -> AggregatedRasterStats:
        """Stream through ``assets`` sequentially and aggregate statistics.

        Each raster is downloaded, opened, clipped, and freed before the
        next one is touched, so peak memory stays bounded to a single
        month's worth of pixels per geometry.
        """
        per_month_means: list[float] = []
        per_month_mins: list[float] = []
        per_month_maxes: list[float] = []
        months_processed = 0

        for asset in assets:
            handle: OpenRasterHandle | None = None
            try:
                req_id = get_request_id()
                logger.info(
                    "ASSET_BEGIN request_id=%s asset=%s/%s/%04d-%02d",
                    req_id, asset.provider, asset.variable, asset.year, asset.month,
                )
                flush()
                # read_raster_from_s3 returns an OpenRasterHandle that
                # owns the dataset + cache lease as a single unit; the
                # finally block closes both atomically. The xr.open_dataset
                # call happens inside raster_cache.open_dataset — its own
                # OPEN_DATASET log line marks the boundary.
                handle = await self.read_raster_from_s3(asset)
                raster = handle.dataset
                logger.info(
                    "DATASET_READY request_id=%s key=%s vars=%s",
                    req_id, handle.lease._key, list(raster.data_vars),
                )
                flush()
                logger.info(
                    "VARIABLE_SELECT_BEGIN request_id=%s variable=%s",
                    req_id, variable,
                )
                flush()
                data = self._select_raster_variable(raster, variable)
                # Eager metadata conversion: use tuple(data.dims) for a
                # DataArray (data.dims is a tuple of dim-name strings;
                # dict(...) raises ValueError). Routed through safe_log
                # so any future formatting failure cannot interrupt the
                # clip+compute path.
                safe_log(
                    logging.INFO,
                    "VARIABLE_SELECT_DONE request_id=%s nc_var=%s dims=%s shape=%s",
                    req_id, data.name, tuple(data.dims), tuple(data.shape),
                )
                flush()
                clip = self._compute_stats_for_geometry(data, geometry)
                per_month_means.append(clip.mean)
                per_month_mins.append(clip.minimum)
                per_month_maxes.append(clip.maximum)
                months_processed += 1
                logger.info(
                    "ASSET_DONE request_id=%s asset=%s/%s/%04d-%02d "
                    "mean=%.6f min=%.6f max=%.6f",
                    req_id, asset.provider, asset.variable, asset.year, asset.month,
                    clip.mean, clip.minimum, clip.maximum,
                )
                flush()
            finally:
                if handle is not None:
                    logger.info(
                        "DATASET_CLOSE_BEGIN request_id=%s key=%s",
                        get_request_id(), handle.lease._key,
                    )
                    handle.close()
                    logger.info(
                        "DATASET_CLOSE_DONE request_id=%s key=%s",
                        get_request_id(), handle.lease._key,
                    )
                    flush()

        if months_processed == 0:
            raise ValueError(
                "No climate data available for the selected period."
            )

        return AggregatedRasterStats(
            months_processed=months_processed,
            mean=float(np.mean(per_month_means)),
            minimum=float(np.min(per_month_mins)),
            maximum=float(np.max(per_month_maxes)),
        )

    async def compute_monthly_series_for_district(
        self,
        district_gid: str,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
        variable: str = "precipitation",
        provider: str = "era5-land",
    ) -> list[MonthlySeriesPoint]:
        """Return per-month raster statistics for a district over a range.

        Mirrors the memory discipline of ``compute_for_district_range``:
        each asset is downloaded, opened, clipped, closed, and unlinked
        before the next iteration so peak memory stays bounded to a
        single month's worth of pixels. The returned list is ordered
        ascending by ``(year, month)`` so the frontend can plot a clean
        chronological series without re-sorting.
        """
        geometry = self.get_district_geometry(district_gid)
        assets = await self.repository.list_by_period_range(
            start_year=start_year,
            start_month=start_month,
            end_year=end_year,
            end_month=end_month,
            provider=provider,
            variable=variable,
        )
        logger.info(
            "Monthly series for district %s: found %d assets between %04d-%02d and %04d-%02d",
            district_gid,
            len(assets),
            start_year,
            start_month,
            end_year,
            end_month,
        )
        if not assets:
            raise ValueError("No climate data available for the selected period.")

        points: list[MonthlySeriesPoint] = []
        for asset in assets:
            handle: OpenRasterHandle | None = None
            try:
                handle = await self.read_raster_from_s3(asset)
                raster = handle.dataset
                data = self._select_raster_variable(raster, variable)
                clip = self._compute_stats_for_geometry(data, geometry)
                points.append(
                    MonthlySeriesPoint(
                        year=asset.year,
                        month=asset.month,
                        mean=clip.mean,
                        min=clip.minimum,
                        max=clip.maximum,
                    )
                )
                logger.info(
                    "Monthly series point %04d-%02d for district %s: mean=%.6f",
                    asset.year,
                    asset.month,
                    district_gid,
                    clip.mean,
                )
            finally:
                if handle is not None:
                    handle.close()

        return points

    async def compute_for_district(
        self,
        district_gid: str,
        provider: str = "era5-land",
        variable: str = "precipitation",
        year: int = 2024,
        month: int = 1,
    ) -> RasterClipResult:
        """Main entry point: compute raster stats for a district (single month)."""
        geometry = self.get_district_geometry(district_gid)

        handle = await self._load_monthly_raster(
            provider=provider, year=year, month=month, variable=variable,
        )
        try:
            raster = handle.dataset
            data = self._select_raster_variable(raster, variable)
            return self._compute_stats_for_geometry(data, geometry)
        finally:
            handle.close()

    async def compute_for_state(
        self,
        state_gid: str,
        provider: str = "era5-land",
        variable: str = "precipitation",
        year: int = 2024,
        month: int = 1,
    ) -> list[StateDistrictStatisticsItem]:
        t_total = time.perf_counter()

        t0 = time.perf_counter()
        adm2 = get_adm2()
        state_districts = adm2[adm2["GID_1"] == state_gid]
        if state_districts.empty:
            raise ValueError(f"State not found: {state_gid}")
        t1 = time.perf_counter()
        logger.info("Load/filter state district geometries: %.3fs", t1 - t0)

        handle = await self._load_monthly_raster(
            provider=provider, year=year, month=month, variable=variable,
        )
        try:
            raster = handle.dataset
            try:
                raster_crs = raster.rio.crs
            except Exception:
                raster_crs = None

            if raster_crs and state_districts.crs and str(state_districts.crs) != str(raster_crs):
                state_districts = state_districts.to_crs(raster_crs)
            elif raster_crs and not state_districts.crs:
                state_districts = state_districts.set_crs(raster_crs)

            data = self._select_raster_variable(raster, variable)

            t_comp_start = time.perf_counter()
            results: list[StateDistrictStatisticsItem] = []
            for idx in state_districts.index:
                t_dist = time.perf_counter()
                district_id = str(state_districts.at[idx, "GID_2"])
                district_geometry = state_districts.loc[[idx]]
                try:
                    stats = self._compute_stats_for_geometry(data, district_geometry)
                    results.append(
                        StateDistrictStatisticsItem(
                            district_id=district_id,
                            mean=stats.mean,
                            min=stats.minimum,
                            max=stats.maximum,
                        )
                    )
                except Exception:
                    logger.exception("Failed computing stats for district %s in state %s", district_id, state_gid)
                    continue
                t_dist_end = time.perf_counter()
                logger.info("District %s: %.3fs", district_id, t_dist_end - t_dist)

            t_comp_end = time.perf_counter()
            logger.info("Compute statistics for all districts: %.3fs", t_comp_end - t_comp_start)

            logger.info(
                "State %s: districts returned=%d computed=%d",
                state_gid,
                len(state_districts),
                len(results),
            )
            for item in results[:5]:
                logger.info("State %s: %s mean=%s", state_gid, item.district_id, item.mean)
            means = [item.mean for item in results]
            if means:
                logger.info("State %s: mean range min=%s max=%s", state_gid, min(means), max(means))
            else:
                logger.info("State %s: mean range unavailable (no computed districts)", state_gid)

            t_total_end = time.perf_counter()
            logger.info("Total compute_for_state: %.3fs", t_total_end - t_total)

            return results
        finally:
            handle.close()

    async def compute_for_district_range(
        self,
        district_gid: str,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
        variable: str = "precipitation",
        provider: str = "era5-land",
    ) -> AggregatedRasterStats:
        """Compute aggregated raster statistics for a single district over a month range.

        Uses ``climate_assets`` as the index: every asset between the
        inclusive ``[start, end]`` month bounds is fetched from PostgreSQL,
        then iterated sequentially. Each NetCDF is downloaded from S3,
        opened, clipped, and freed before the next is touched.
        """
        geometry = self.get_district_geometry(district_gid)
        assets = await self.repository.list_by_period_range(
            start_year=start_year,
            start_month=start_month,
            end_year=end_year,
            end_month=end_month,
            provider=provider,
            variable=variable,
        )
        logger.info(
            "Range query for district %s: found %d assets between %04d-%02d and %04d-%02d",
            district_gid,
            len(assets),
            start_year,
            start_month,
            end_year,
            end_month,
        )
        return await self._aggregate_for_geometry(assets, geometry, variable)

    async def compute_for_state_range(
        self,
        state_gid: str,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
        variable: str = "precipitation",
        provider: str = "era5-land",
    ) -> tuple[int, list[StateDistrictStatisticsItem]]:
        """Compute aggregated per-district statistics for a state over a month range.

        Returns ``(months_processed, districts)`` so the router can surface
        the same ``months_processed`` count regardless of whether any
        district actually has data — callers can detect an empty range by
        checking ``months_processed == 0`` before consulting the districts
        list.

        For every month in the range, the state-wide raster is downloaded
        once and clipped against every district geometry, matching the
        single-month ``compute_for_state`` behaviour. NetCDF files are
        released between months so peak memory stays bounded to one
        raster at a time.
        """
        t_total = time.perf_counter()

        t0 = time.perf_counter()
        adm2 = get_adm2()
        state_districts = adm2[adm2["GID_1"] == state_gid]
        if state_districts.empty:
            raise ValueError(f"State not found: {state_gid}")
        t1 = time.perf_counter()
        logger.info("Load/filter state district geometries: %.3fs", t1 - t0)

        assets = await self.repository.list_by_period_range(
            start_year=start_year,
            start_month=start_month,
            end_year=end_year,
            end_month=end_month,
            provider=provider,
            variable=variable,
        )
        logger.info(
            "Range query for state %s: found %d assets between %04d-%02d and %04d-%02d",
            state_gid,
            len(assets),
            start_year,
            start_month,
            end_year,
            end_month,
        )

        if not assets:
            raise ValueError("No climate data available for the selected period.")

        per_month_district_stats: list[dict[str, tuple[float, float, float]]] = []
        months_processed = 0

        for asset in assets:
            handle: OpenRasterHandle | None = None
            try:
                handle = await self.read_raster_from_s3(asset)
                raster = handle.dataset
                try:
                    raster_crs = raster.rio.crs
                except Exception:
                    raster_crs = None

                districts_for_month = state_districts
                if raster_crs and districts_for_month.crs and str(districts_for_month.crs) != str(raster_crs):
                    districts_for_month = districts_for_month.to_crs(raster_crs)
                elif raster_crs and not districts_for_month.crs:
                    districts_for_month = districts_for_month.set_crs(raster_crs)

                data = self._select_raster_variable(raster, variable)

                per_district: dict[str, tuple[float, float, float]] = {}
                for idx in districts_for_month.index:
                    district_id = str(districts_for_month.at[idx, "GID_2"])
                    district_geometry = districts_for_month.loc[[idx]]
                    try:
                        clip = self._compute_stats_for_geometry(data, district_geometry)
                        per_district[district_id] = (clip.mean, clip.minimum, clip.maximum)
                    except Exception:
                        logger.exception(
                            "Failed computing stats for district %s in state %s",
                            district_id,
                            state_gid,
                        )
                        continue

                per_month_district_stats.append(per_district)
                months_processed += 1
                logger.info(
                    "Aggregated month %04d-%02d for state %s: districts=%d",
                    asset.year,
                    asset.month,
                    state_gid,
                    len(per_district),
                )
            finally:
                if handle is not None:
                    handle.close()

        # Reduce across months: per-district mean / min-of-mins / max-of-maxes.
        all_district_ids: set[str] = set()
        for month_stats in per_month_district_stats:
            all_district_ids.update(month_stats.keys())

        districts: list[StateDistrictStatisticsItem] = []
        for district_id in sorted(all_district_ids):
            means: list[float] = []
            mins: list[float] = []
            maxes: list[float] = []
            for month_stats in per_month_district_stats:
                stats = month_stats.get(district_id)
                if stats is None:
                    continue
                mean_value, min_value, max_value = stats
                means.append(mean_value)
                mins.append(min_value)
                maxes.append(max_value)
            if not means:
                continue
            districts.append(
                StateDistrictStatisticsItem(
                    district_id=district_id,
                    mean=float(np.mean(means)),
                    min=float(np.min(mins)),
                    max=float(np.max(maxes)),
                )
            )

        logger.info(
            "State %s: months_processed=%d districts_returned=%d",
            state_gid,
            months_processed,
            len(districts),
        )

        t_total_end = time.perf_counter()
        logger.info("Total compute_for_state_range: %.3fs", t_total_end - t_total)

        return months_processed, districts
