"""Geospatial computation for ERA5 climate data."""
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

    Transitional helper for callers that acquired the dataset and lease
    independently. New callers should use OpenRasterHandle instead and
    call handle.close() so dataset/lease ownership is bundled.

    Called inside finally blocks so file handles are never leaked even
    when an exception is raised mid-iteration.
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
        # Eager metadata conversion: tuple(data.dims), not dict(...), since
        # data.dims is a tuple of dim-name strings for a DataArray. Routed
        # through safe_log so a formatting failure can't interrupt the
        # clip+compute path.
        safe_log(
            logging.INFO,
            "RIO_CLIP_BEGIN request_id=%s dims=%s shape=%s",
            req_id, tuple(data.dims), tuple(data.shape),
        )
        flush()
        try:
            # all_touched=True includes any cell that overlaps the polygon
            # at all, not just cells whose center falls inside it. Needed
            # for small/compact districts (e.g. dense urban areas like
            # Kolkata) that can be smaller than a single ERA5-Land cell
            # (~0.1deg / ~11km) -- without this, such districts match zero
            # cells under the default center-point rule and raise
            # NoDataInBounds even though they're valid land, not ocean.
            clipped = data.rio.clip(geometry.geometry.values, geometry.crs, all_touched=True)
        except rioxarray.exceptions.NoDataInBounds:
            # Genuine case: every overlapping cell is masked (ocean/no-data
            # in the source), not a resolution artifact -- e.g. a coastal
            # district with no ERA5-Land cell overlapping it at all. Return
            # a zero-coverage result rather than a 500.
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
        """Load district geometry by GID_2 from GADM."""
        adm2 = get_adm2()
        district = adm2[adm2["GID_2"] == district_gid]
        if district.empty:
            raise ValueError(f"District not found: {district_gid}")
        return district

    async def read_raster_from_s3(self, asset: ClimateAsset) -> OpenRasterHandle:
        """Resolve ``asset`` through the shared raster cache and open it.

        The local cache file at
        Settings.raster_cache_root_resolved()/{provider}/{variable}/{YYYY}/{MM}.nc
        is reused across requests; concurrent requests for the same asset
        coalesce into one S3 download via the cache's per-key single-flight,
        and opens are serialised per storage key via
        application.raster_cache.RasterCache.open_dataset.

        Returns an OpenRasterHandle bundling the opened dataset, cache
        path, and active RasterLease. The caller MUST close the handle
        once done so the file handle is dropped and the cache file
        becomes eviction-eligible.

        Exception safety: if open_dataset (or write_crs) raises after
        acquire succeeds, the lease is released here so the cache file
        isn't protected forever with no reader.
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
        """Fetch/open/clip up to ``concurrency`` months in parallel and aggregate.

        Same rationale as compute_monthly_series_for_district: the S3
        download dominates per-month cost and is pure I/O wait, so
        overlapping several downloads meaningfully cuts wall-clock time on
        multi-year ranges. Actual netCDF opens remain serialized via
        RasterCache.open_dataset's NATIVE_IO_LOCK regardless of
        concurrency, so this does not reintroduce the concurrent-access
        crash. Peak memory scales with concurrency, not range length.
        """
        assets = list(assets)
        semaphore = asyncio.Semaphore(concurrency)
        t_request_start = time.perf_counter()

        async def _process_one(asset: ClimateAsset) -> tuple[tuple[float, float, float] | None, float, float]:
            async with semaphore:
                handle: OpenRasterHandle | None = None
                t_fetch_start = time.perf_counter()
                try:
                    req_id = get_request_id()
                    logger.info(
                        "ASSET_BEGIN request_id=%s asset=%s/%s/%04d-%02d",
                        req_id, asset.provider, asset.variable, asset.year, asset.month,
                    )
                    flush()
                    handle = await self.read_raster_from_s3(asset)
                    t_fetch_open = time.perf_counter() - t_fetch_start
                    raster = handle.dataset
                    logger.info(
                        "DATASET_READY request_id=%s key=%s vars=%s",
                        req_id, handle.lease._key, list(raster.data_vars),
                    )
                    flush()
                    data = self._select_raster_variable(raster, variable)
                    safe_log(
                        logging.INFO,
                        "VARIABLE_SELECT_DONE request_id=%s nc_var=%s dims=%s shape=%s",
                        req_id, data.name, tuple(data.dims), tuple(data.shape),
                    )
                    flush()
                    t_clip_start = time.perf_counter()
                    clip = self._compute_stats_for_geometry(data, geometry)
                    t_clip = time.perf_counter() - t_clip_start
                    logger.info(
                        "ASSET_DONE request_id=%s asset=%s/%s/%04d-%02d "
                        "mean=%.6f min=%.6f max=%.6f fetch_open_seconds=%.3f clip_seconds=%.3f",
                        req_id, asset.provider, asset.variable, asset.year, asset.month,
                        clip.mean, clip.minimum, clip.maximum, t_fetch_open, t_clip,
                    )
                    flush()
                    return (clip.mean, clip.minimum, clip.maximum), t_fetch_open, t_clip
                finally:
                    if handle is not None:
                        await handle.aclose()
                        flush()

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
        """Return per-month raster statistics for a district over a range.

        Fetches/opens up to ``concurrency`` months in parallel. The S3
        download is the dominant cost per month (~0.6-0.7s) and is pure
        I/O wait, so overlapping several downloads gives a large wall-clock
        win on multi-year ranges. The actual netCDF/HDF5 open still goes
        through RasterCache.open_dataset's NATIVE_IO_LOCK, so opens remain
        strictly serialized regardless of how many months are in flight --
        this does not reintroduce the concurrent-access crash.

        Peak memory scales with ``concurrency`` (each in-flight month holds
        one open dataset), not with the total range length.
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
            district_gid, len(assets), start_year, start_month, end_year, end_month,
        )
        if not assets:
            raise ValueError("No climate data available for the selected period.")

        semaphore = asyncio.Semaphore(concurrency)
        t_request_start = time.perf_counter()

        async def _process_one(asset: ClimateAsset) -> tuple[MonthlySeriesPoint, float, float]:
            async with semaphore:
                handle: OpenRasterHandle | None = None
                t_fetch_start = time.perf_counter()
                try:
                    handle = await self.read_raster_from_s3(asset)
                    t_fetch_open = time.perf_counter() - t_fetch_start
                    raster = handle.dataset
                    data = self._select_raster_variable(raster, variable)
                    t_clip_start = time.perf_counter()
                    clip = self._compute_stats_for_geometry(data, geometry)
                    t_clip = time.perf_counter() - t_clip_start
                    logger.info(
                        "Monthly series point %04d-%02d for district %s: mean=%.6f "
                        "fetch_open_seconds=%.3f clip_seconds=%.3f",
                        asset.year, asset.month, district_gid, clip.mean,
                        t_fetch_open, t_clip,
                    )
                    point = MonthlySeriesPoint(
                        year=asset.year,
                        month=asset.month,
                        mean=clip.mean,
                        min=clip.minimum,
                        max=clip.maximum,
                    )
                    return point, t_fetch_open, t_clip
                finally:
                    if handle is not None:
                        await handle.aclose()

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
        """Compute aggregated raster statistics for a single district over a month range.

        Uses climate_assets as the index: every asset between the
        inclusive [start, end] month bounds is fetched from PostgreSQL,
        then iterated sequentially. Each NetCDF is downloaded, opened,
        clipped, and freed before the next is touched.
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
        """Compute aggregated per-district statistics for a state over a month range.

        Returns (months_processed, districts) so the router can surface
        the same months_processed count regardless of whether any
        district has data. For every month in the range, the state-wide
        raster is downloaded once and clipped against every district
        geometry; files are released between months so peak memory stays
        bounded to one raster at a time.
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
            state_gid, len(assets), start_year, start_month, end_year, end_month,
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