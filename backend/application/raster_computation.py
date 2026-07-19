from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import Iterable

import geopandas as gpd
import numpy as np
import rioxarray
import xarray as xr
import concurrent.futures

_raster_process_pool = concurrent.futures.ProcessPoolExecutor()

def _worker_open_and_compute(path: str, variable: str, geometry: gpd.GeoDataFrame):
    import xarray as xr
    import numpy as np
    import rioxarray
    try:
        rds = xr.open_dataset(path)
        rds = rds.rio.write_crs("EPSG:4326")
        nc_var = VARIABLE_MAP.get(variable)
        if nc_var is None:
            raise ValueError(f"Unknown variable '{variable}'")
        data = rds[nc_var]
        if len(data.dims) == 3 and data.shape[0] == 1:
            data = data.isel({data.dims[0]: 0})
        try:
            clipped = data.rio.clip(geometry.geometry.values, geometry.crs, all_touched=True)
        except rioxarray.exceptions.NoDataInBounds:
            return RasterClipResult(0, 0, 0.0, tuple(geometry.total_bounds), 0.0, 0.0, 0.0)
        bounds = clipped.rio.bounds()
        values = clipped.values
        total_pixels = values.size
        valid_mask = ~np.isnan(values)
        valid_pixel_count = int(valid_mask.sum())
        valid_pixel_percentage = (valid_pixel_count / total_pixels * 100) if total_pixels > 0 else 0.0
        valid_data = values[valid_mask]
        mean_value = float(np.mean(valid_data)) if valid_pixel_count > 0 else 0.0
        min_value = float(np.min(valid_data)) if valid_pixel_count > 0 else 0.0
        max_value = float(np.max(valid_data)) if valid_pixel_count > 0 else 0.0
        return RasterClipResult(int(total_pixels), valid_pixel_count, float(valid_pixel_percentage), bounds, mean_value, min_value, max_value)
    finally:
        try:
            rds.close()
        except:
            pass


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
    """The final stats for a district or state over a period of several months."""
    months_processed: int
    mean: float
    minimum: float
    maximum: float


def _close_and_cleanup(raster: xr.Dataset | xr.DataArray | None, lease: RasterLease | None) -> None:
    """A cleanup helper that makes sure we close files and release our 'leases'."""
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
        self._raster_cache = raster_cache or RasterCache()

    async def _load_monthly_raster(
        self,
        provider: str,
        year: int,
        month: int,
        variable: str,
    ) -> OpenRasterHandle:
        """Finds a specific data file in our database and opens it from the cache."""
        t0 = time.perf_counter()
        asset = await self.repository.get_by_period(year, month, provider, variable)
        t1 = time.perf_counter()
        logger.info("SQLite asset lookup: %.3fs", t1 - t0)
        if not asset:
            raise ValueError(f"No dataset found for {provider}/{year}/{month:02d}")
        return await self.read_raster_from_storage(asset)

    def _select_raster_variable(
        self,
        raster: xr.Dataset | xr.DataArray,
        variable: str,
    ) -> xr.DataArray:
        """Picks the correct variable (like 'precipitation') out of a data file."""
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
        """The core 'cookie-cutter' logic: cuts the data to a district's shape and calculates stats."""
        req_id = get_request_id()
        safe_log(
            logging.INFO,
            "RIO_CLIP_BEGIN request_id=%s dims=%s shape=%s",
            req_id, tuple(data.dims), tuple(data.shape),
        )
        flush()
        try:
            # We use 'all_touched=True' to make sure we don't miss tiny districts 
            # that might be smaller than a single square on our climate map. 
            # This is really important for cities like Kolkata.
            clipped = data.rio.clip(geometry.geometry.values, geometry.crs, all_touched=True)
        except rioxarray.exceptions.NoDataInBounds:
            # If a district is completely outside our data range (like a far-off island 
            # or deep in the ocean), we return zeros instead of crashing.
            logger.warning(
                "RIO_CLIP_NO_DATA request_id=%s -- no valid cells even with all_touched=True",
                req_id,
            )
            return RasterClipResult(
                pixel_count=0,
                valid_pixel_count=0,
                valid_pixel_percentage=0.0,
                bounds=tuple(geometry.total_bounds),
                mean=0.0,
                minimum=0.0,
                maximum=0.0,
            )
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
        """Looks up the official boundary for a district using its id."""
        adm2 = get_adm2()
        district = adm2[adm2["GID_2"] == district_gid]
        if district.empty:
            raise ValueError(f"District not found: {district_gid}")
        return district

    async def read_raster_from_storage(self, asset: ClimateAsset) -> OpenRasterHandle:
        """Open a data file from local storage, warming the local cache if needed."""
        lease = await self._raster_cache.acquire(asset, self.storage)
        try:
            rds = await self._raster_cache.open_dataset(lease, asset=asset)
            logger.info(
                "Opened raster file: %s  vars=%s",
                lease.path, list(rds.data_vars),
            )
            rds = rds.rio.write_crs("EPSG:4326")
        except Exception:
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
        concurrency: int = 6,
    ) -> AggregatedRasterStats:
        """Fetch/open/clip up to ``concurrency`` months in parallel and aggregate."""
        assets = list(assets)
        semaphore = asyncio.Semaphore(concurrency)
        t_request_start = time.perf_counter()

        async def _process_one(asset: ClimateAsset) -> tuple[tuple[float, float, float] | None, float, float]:
            async with semaphore:
                t_fetch_start = time.perf_counter()
                try:
                    req_id = get_request_id()
                    logger.info("ASSET_BEGIN request_id=%s asset=%s/%s/%04d-%02d", req_id, asset.provider, asset.variable, asset.year, asset.month)
                    flush()
                    lease = await self._raster_cache.acquire(asset, self.storage)
                    t_fetch_open = time.perf_counter() - t_fetch_start
                    
                    t_clip_start = time.perf_counter()
                    loop = asyncio.get_running_loop()
                    clip = await loop.run_in_executor(
                        _raster_process_pool,
                        _worker_open_and_compute,
                        str(lease.path),
                        variable,
                        geometry
                    )
                    t_clip = time.perf_counter() - t_clip_start
                    logger.info("ASSET_DONE request_id=%s mean=%.6f fetch_open=%.3f clip=%.3f", req_id, clip.mean, t_fetch_open, t_clip)
                    flush()
                    return (clip.mean, clip.minimum, clip.maximum), t_fetch_open, t_clip
                finally:
                    try:
                        lease.release()
                    except Exception:
                        pass

        results = await asyncio.gather(*(_process_one(asset) for asset in assets))

        per_month_means = [r[0][0] for r in results if r[0] is not None]
        per_month_mins = [r[0][1] for r in results if r[0] is not None]
        per_month_maxes = [r[0][2] for r in results if r[0] is not None]
        months_processed = len(per_month_means)
        total_fetch_open = sum(r[1] for r in results)
        total_clip = sum(r[2] for r in results)
        wall_seconds = time.perf_counter() - t_request_start
        sequential_equivalent = total_fetch_open + total_clip
        logger.info(
            "TIMING_SUMMARY endpoint=district_range months=%d concurrency=%d "
            "wall_seconds=%.2f total_fetch_open_seconds=%.2f total_clip_seconds=%.2f "
            "sequential_equivalent_seconds=%.2f speedup=%.2fx",
            months_processed, concurrency, wall_seconds,
            total_fetch_open, total_clip, sequential_equivalent,
            (sequential_equivalent / wall_seconds) if wall_seconds > 0 else 0.0,
        )

        if months_processed == 0:
            raise ValueError("No climate data available for the selected period.")

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
        concurrency: int = 6,
    ) -> list[MonthlySeriesPoint]:
        """Return per-month raster statistics for a district over a range."""
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
            district_gid, len(assets), start_year, start_month, end_year, end_month,
        )
        if not assets:
            raise ValueError("No climate data available for the selected period.")

        semaphore = asyncio.Semaphore(concurrency)
        t_request_start = time.perf_counter()

        async def _process_one(asset: ClimateAsset) -> tuple[MonthlySeriesPoint, float, float]:
            async with semaphore:
                t_fetch_start = time.perf_counter()
                try:
                    lease = await self._raster_cache.acquire(asset, self.storage)
                    t_fetch_open = time.perf_counter() - t_fetch_start
                    
                    t_clip_start = time.perf_counter()
                    loop = asyncio.get_running_loop()
                    clip = await loop.run_in_executor(
                        _raster_process_pool,
                        _worker_open_and_compute,
                        str(lease.path),
                        variable,
                        geometry
                    )
                    t_clip = time.perf_counter() - t_clip_start
                    logger.info("Monthly point %04d-%02d mean=%.6f", asset.year, asset.month, clip.mean)
                    point = MonthlySeriesPoint(
                        year=asset.year, month=asset.month,
                        mean=clip.mean, min=clip.minimum, max=clip.maximum
                    )
                    return point, t_fetch_open, t_clip
                finally:
                    try:
                        lease.release()
                    except Exception:
                        pass

        results = await asyncio.gather(*(_process_one(asset) for asset in assets))

        points = [r[0] for r in results]
        total_fetch_open = sum(r[1] for r in results)
        total_clip = sum(r[2] for r in results)
        wall_seconds = time.perf_counter() - t_request_start
        # sequential_equivalent is what wall time would have been at
        # concurrency=1 -- compares directly against the old behaviour.
        sequential_equivalent = total_fetch_open + total_clip
        logger.info(
            "TIMING_SUMMARY endpoint=district_time_series district_id=%s months=%d "
            "concurrency=%d wall_seconds=%.2f total_fetch_open_seconds=%.2f "
            "total_clip_seconds=%.2f sequential_equivalent_seconds=%.2f speedup=%.2fx "
            "avg_fetch_open_per_month=%.3f avg_clip_per_month=%.4f",
            district_gid, len(points), concurrency, wall_seconds,
            total_fetch_open, total_clip, sequential_equivalent,
            (sequential_equivalent / wall_seconds) if wall_seconds > 0 else 0.0,
            total_fetch_open / len(points) if points else 0.0,
            total_clip / len(points) if points else 0.0,
        )

        # gather preserves call order, which matches assets' query order,
        # but sort explicitly so callers never depend on repository ordering.
        return sorted(points, key=lambda p: (p.year, p.month))

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
            await handle.aclose()

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
                state_gid, len(state_districts), len(results),
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
            await handle.aclose()

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
        """Compute aggregated raster statistics for a single district over a month range."""
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
            district_gid, len(assets), start_year, start_month, end_year, end_month,
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
        """Compute aggregated per-district statistics for a state over a month range."""
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
            state_gid, len(assets), start_year, start_month, end_year, end_month,
        )

        if not assets:
            raise ValueError("No climate data available for the selected period.")

        per_month_district_stats: list[dict[str, tuple[float, float, float]]] = []
        months_processed = 0

        for asset in assets:
            handle: OpenRasterHandle | None = None
            try:
                handle = await self.read_raster_from_storage(asset)
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
                            district_id, state_gid,
                        )
                        continue

                per_month_district_stats.append(per_district)
                months_processed += 1
                logger.info(
                    "Aggregated month %04d-%02d for state %s: districts=%d",
                    asset.year, asset.month, state_gid, len(per_district),
                )
            finally:
                if handle is not None:
                    await handle.aclose()

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
            state_gid, months_processed, len(districts),
        )

        t_total_end = time.perf_counter()
        logger.info("Total compute_for_state_range: %.3fs", t_total_end - t_total)

        return months_processed, districts
