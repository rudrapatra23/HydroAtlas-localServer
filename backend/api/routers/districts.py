from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_district_clipper, get_repository, get_storage
from application.diagnostics import flush, request_context
from application.dto.requests import StatisticsRequest
from application.dto.responses import (
    DistrictMonthlySeriesResponse,
    DistrictRasterClipResponse,
    StatisticsResponse,
)
from application.raster_computation import RasterComputation
from district_clip import DistrictNotFoundError, Era5DistrictClipper
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort
import re

from application.dto.responses import DistrictRasterClipRangeResponse

_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _parse_year_month(value: str, *, param_name: str) -> tuple[int, int]:
    """Parse a 'YYYY-MM' query string into (year, month)."""
    match = _YEAR_MONTH_RE.match(value)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{param_name}' must be in YYYY-MM format, got '{value}'.",
        )
    year, month = int(match.group(1)), int(match.group(2))
    if not (1 <= month <= 12):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{param_name}' month must be between 01 and 12, got '{value}'.",
        )
    return year, month


def _month_range(start_year: int, start_month: int, end_year: int, end_month: int):
    """Yield (year, month) tuples from start to end, inclusive."""
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1
logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/districts", tags=["districts"])


async def get_raster_computation(
    repository: Annotated[DatasetRepository, Depends(get_repository)],
    storage: Annotated[StoragePort, Depends(get_storage)],
) -> RasterComputation:
    return RasterComputation(repository, storage)


# Everything here works month by month. When you ask for a range of dates, 
# we look into our PostgreSQL database to see what data files we have for 
# those months. Then, we pull each file from S3 one by one and combine 
# the numbers to give you the final statistics.
_NO_PERIOD_MESSAGE = "No climate data available for the selected period."


def _validate_range_against_inventory(
    request: StatisticsRequest,
    available: tuple[int, int, int, int] | None,
) -> None:
    """Check if the requested dates actually have data available.

    Raises:
        HTTPException: 400 when the inventory is empty or the requested
            range starts before the earliest asset or ends after the
            latest asset.
    """
    if available is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_NO_PERIOD_MESSAGE,
        )
    min_year, min_month, max_year, max_month = available
    start_key = request.start_year * 12 + (request.start_month - 1)
    end_key = request.end_year * 12 + (request.end_month - 1)
    min_key = min_year * 12 + (min_month - 1)
    max_key = max_year * 12 + (max_month - 1)
    if start_key < min_key or end_key > max_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Selected month range falls outside the available dataset "
                f"range {min_year:04d}-{min_month:02d} to {max_year:04d}-{max_month:02d}."
            ),
        )


@router.post("/{district_id}/statistics", response_model=StatisticsResponse)
async def get_district_statistics(
    district_id: str,
    request: StatisticsRequest,
    computation: Annotated[RasterComputation, Depends(get_raster_computation)],
) -> StatisticsResponse:
    """Calculate the average, minimum, and maximum climate values for a specific district over a period of time."""
    with request_context() as req_id:
        logger.info(
            "REQUEST_BEGIN endpoint=district_statistics request_id=%s "
            "district_id=%s variable=%s start=%04d-%02d end=%04d-%02d "
            "pid=%d thread_id=%d task_id=%s",
            req_id, district_id, request.variable,
            request.start_year, request.start_month,
            request.end_year, request.end_month,
            os.getpid(), threading.get_ident(), id(asyncio.current_task()),
        )
        flush()
        try:
            try:
                request.validate()
            except ValueError as exc:
                logger.info(
                    "REQUEST_ERROR endpoint=district_statistics request_id=%s "
                    "error=validation pid=%d thread_id=%d",
                    req_id, os.getpid(), threading.get_ident(),
                )
                flush()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                )

            available = await computation.repository.get_available_range(
                provider="era5-land",
                variable=request.variable,
            )
            _validate_range_against_inventory(request, available)

            try:
                aggregated = await computation.compute_for_district_range(
                    district_gid=district_id,
                    start_year=request.start_year,
                    start_month=request.start_month,
                    end_year=request.end_year,
                    end_month=request.end_month,
                    variable=request.variable,
                )
            except ValueError as exc:
                message = str(exc)
                if message == _NO_PERIOD_MESSAGE:
                    logger.info(
                        "REQUEST_ERROR endpoint=district_statistics request_id=%s "
                        "error=no_period pid=%d thread_id=%d",
                        req_id, os.getpid(), threading.get_ident(),
                    )
                    flush()
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=_NO_PERIOD_MESSAGE,
                    )
                logger.info(
                    "REQUEST_ERROR endpoint=district_statistics request_id=%s "
                    "error=value_error detail=%s pid=%d thread_id=%d",
                    req_id, message, os.getpid(), threading.get_ident(),
                )
                flush()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=message,
                )

            logger.info(
                "REQUEST_END endpoint=district_statistics request_id=%s "
                "district_id=%s months_processed=%d pid=%d thread_id=%d task_id=%s",
                req_id, district_id, aggregated.months_processed,
                os.getpid(), threading.get_ident(), id(asyncio.current_task()),
            )
            flush()
            return StatisticsResponse(
                district_id=district_id,
                variable=request.variable,
                start_year=request.start_year,
                start_month=request.start_month,
                end_year=request.end_year,
                end_month=request.end_month,
                months_processed=aggregated.months_processed,
                mean=aggregated.mean,
                min=aggregated.minimum,
                max=aggregated.maximum,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "REQUEST_ERROR endpoint=district_statistics request_id=%s "
                "error=unhandled pid=%d thread_id=%d task_id=%s",
                req_id, os.getpid(), threading.get_ident(), id(asyncio.current_task()),
            )
            flush()
            raise


@router.post("/{district_id}/time-series", response_model=DistrictMonthlySeriesResponse)
async def get_district_time_series(
    district_id: str,
    request: StatisticsRequest,
    computation: Annotated[RasterComputation, Depends(get_raster_computation)],
) -> DistrictMonthlySeriesResponse:
    """Fetch monthly climate stats for a district. This gives you a data point 
    for every single month in your requested range.

    This data is perfect for building charts, showing trends, or exporting 
    to CSV. Each point includes the year, month, and the core stats 
    (mean, min, max), so the frontend has everything it needs to 
    display the data without asking for more.
    """
    with request_context() as req_id:
        logger.info(
            "REQUEST_BEGIN endpoint=district_time_series request_id=%s "
            "district_id=%s variable=%s start=%04d-%02d end=%04d-%02d "
            "pid=%d thread_id=%d task_id=%s",
            req_id, district_id, request.variable,
            request.start_year, request.start_month,
            request.end_year, request.end_month,
            os.getpid(), threading.get_ident(), id(asyncio.current_task()),
        )
        flush()
        try:
            try:
                request.validate()
            except ValueError as exc:
                logger.info(
                    "REQUEST_ERROR endpoint=district_time_series request_id=%s "
                    "error=validation pid=%d thread_id=%d",
                    req_id, os.getpid(), threading.get_ident(),
                )
                flush()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                )

            available = await computation.repository.get_available_range(
                provider="era5-land",
                variable=request.variable,
            )
            _validate_range_against_inventory(request, available)

            try:
                points = await computation.compute_monthly_series_for_district(
                    district_gid=district_id,
                    start_year=request.start_year,
                    start_month=request.start_month,
                    end_year=request.end_year,
                    end_month=request.end_month,
                    variable=request.variable,
                )
            except ValueError as exc:
                message = str(exc)
                if message == _NO_PERIOD_MESSAGE:
                    logger.info(
                        "REQUEST_ERROR endpoint=district_time_series request_id=%s "
                        "error=no_period pid=%d thread_id=%d",
                        req_id, os.getpid(), threading.get_ident(),
                    )
                    flush()
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=_NO_PERIOD_MESSAGE,
                    )
                logger.info(
                    "REQUEST_ERROR endpoint=district_time_series request_id=%s "
                    "error=value_error detail=%s pid=%d thread_id=%d",
                    req_id, message, os.getpid(), threading.get_ident(),
                )
                flush()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=message,
                )

            logger.info(
                "REQUEST_END endpoint=district_time_series request_id=%s "
                "district_id=%s months_processed=%d pid=%d thread_id=%d task_id=%s",
                req_id, district_id, len(points),
                os.getpid(), threading.get_ident(), id(asyncio.current_task()),
            )
            flush()
            return DistrictMonthlySeriesResponse(
                district_id=district_id,
                variable=request.variable,
                start_year=request.start_year,
                start_month=request.start_month,
                end_year=request.end_year,
                end_month=request.end_month,
                months_processed=len(points),
                points=points,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "REQUEST_ERROR endpoint=district_time_series request_id=%s "
                "error=unhandled pid=%d thread_id=%d task_id=%s",
                req_id, os.getpid(), threading.get_ident(), id(asyncio.current_task()),
            )
            flush()
            raise


@router.get(
    "/{district_id}/raster-clip",
    response_model=DistrictRasterClipResponse,
    name="district_raster_clip",
)
async def get_district_raster_clip(
    district_id: str,
    clipper: Annotated[Era5DistrictClipper, Depends(get_district_clipper)],
    year: Annotated[int, Query(ge=1900, le=2100, description="ERA5 year (YYYY).")],
    month: Annotated[int, Query(ge=1, le=12, description="ERA5 month (1-12).")],
    variable: Annotated[
        str,
        Query(
            description=(
                "HydroAtlas public variable name. One of "
                "'precipitation', 'soil_moisture', 'surface_runoff'."
            ),
        ),
    ] = "precipitation",
    padding_deg: Annotated[
        float,
        Query(
            ge=0.0,
            le=2.0,
            description=(
                "Bbox padding in degrees added to the district polygon "
                "before the Stage-2 NetCDF read (Stage 1 I/O buffer)."
            ),
        ),
    ] = 0.1,
    provider: Annotated[
        str,
        Query(description="Climate data provider key."),
    ] = "era5-land",
) -> DistrictRasterClipResponse:
    """Take the climate data and 'cookie-cutter' it to fit exactly within a 
    district's borders.

    First, we look up the precise shape of the district. Then, we fetch 
    the right data file from our cache. We do a two-step process to clip 
    the data: first, we narrow it down to the area around the district, 
    and then we trim it exactly to the district's borders. Finally, we 
    send back the clipped data as GeoJSON along with some helpful stats.

    For the cells that sit right on the border, we keep their original 
    values but trim their shapes. Anything completely outside the 
    district is left out.
    """
    with request_context() as req_id:
        logger.info(
            "REQUEST_BEGIN endpoint=district_raster_clip request_id=%s "
            "district_id=%s variable=%s year=%04d month=%02d padding_deg=%.3f "
            "provider=%s pid=%d thread_id=%d task_id=%s",
            req_id, district_id, variable, year, month, padding_deg, provider,
            os.getpid(), threading.get_ident(), id(asyncio.current_task()),
        )
        flush()
        try:
            try:
                result = await clipper.clip(
                    district_id=district_id,
                    year=year,
                    month=month,
                    variable=variable,
                    padding_deg=padding_deg,
                    provider=provider,
                )
            except DistrictNotFoundError as exc:
                logger.info(
                    "REQUEST_ERROR endpoint=district_raster_clip request_id=%s "
                    "error=district_not_found pid=%d thread_id=%d",
                    req_id, os.getpid(), threading.get_ident(),
                )
                flush()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=str(exc),
                )
            except KeyError as exc:
                # Unknown HydroAtlas variable name.
                logger.info(
                    "REQUEST_ERROR endpoint=district_raster_clip request_id=%s "
                    "error=unknown_variable detail=%s pid=%d thread_id=%d",
                    req_id, exc, os.getpid(), threading.get_ident(),
                )
                flush()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                )
            except ValueError as exc:
                message = str(exc)
                # Asset-missing vs. bbox-misses-raster are both 404 from
                # the user's perspective; the message is enough to tell
                # them which one happened.
                logger.info(
                    "REQUEST_ERROR endpoint=district_raster_clip request_id=%s "
                    "error=value_error detail=%s pid=%d thread_id=%d",
                    req_id, message, os.getpid(), threading.get_ident(),
                )
                flush()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=message,
                )
            except FileNotFoundError as exc:
                logger.error(
                    "REQUEST_ERROR endpoint=district_raster_clip request_id=%s "
                    "error=netcdf_missing detail=%s pid=%d thread_id=%d",
                    req_id, exc, os.getpid(), threading.get_ident(),
                )
                flush()
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "NetCDF file is not available on disk and the S3 "
                        "download failed. Check the storage adapter and "
                        "the climate_assets row for this period."
                    ),
                )

            # Enforce the max_features safety cap so a runaway request
            # cannot OOM the process; we never silently truncate.
            n_features = len(result.feature_collection.get("features", []))
            if n_features > clipper.max_features:
                logger.warning(
                    "REQUEST_ERROR endpoint=district_raster_clip request_id=%s "
                    "error=too_many_features features=%d max=%d",
                    req_id, n_features, clipper.max_features,
                )
                flush()
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"District yields {n_features} clipped cells, which "
                        f"exceeds the configured maximum of "
                        f"{clipper.max_features}. Re-run with a smaller "
                        "bbox padding or split the district."
                    ),
                )

            logger.info(
                "REQUEST_END endpoint=district_raster_clip request_id=%s "
                "district_id=%s variable=%s year=%04d month=%02d "
                "valid_cells=%d boundary_cells=%d "
                "asset=%s cache_hit=%s "
                "request_duration=%.3fs pid=%d thread_id=%d task_id=%s",
                req_id, district_id, variable, year, month,
                result.summary.get("valid_cells", 0),
                result.summary.get("boundary_cells", 0),
                result.asset_storage_key,
                result.cache_hit,
                result.diagnostics.get("request_duration_seconds", 0.0),
                os.getpid(), threading.get_ident(), id(asyncio.current_task()),
            )
            flush()
            return DistrictRasterClipResponse.from_domain(result)
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "REQUEST_ERROR endpoint=district_raster_clip request_id=%s "
                "error=unhandled pid=%d thread_id=%d task_id=%s",
                req_id, os.getpid(), threading.get_ident(), id(asyncio.current_task()),
            )
            flush()
            raise
@router.get(
    "/{district_id}/raster-clip-range",
    response_model=DistrictRasterClipRangeResponse,
    name="district_raster_clip_range",
)
async def get_district_raster_clip_range(
    district_id: str,
    clipper: Annotated[Era5DistrictClipper, Depends(get_district_clipper)],
    computation: Annotated[RasterComputation, Depends(get_raster_computation)],
    start: Annotated[str, Query(description="Start month, format YYYY-MM.")],
    end: Annotated[str, Query(description="End month, format YYYY-MM.")],
    variable: Annotated[
        str,
        Query(
            description=(
                "HydroAtlas public variable name. One of "
                "'precipitation', 'soil_moisture', 'surface_runoff'."
            ),
        ),
    ] = "precipitation",
    padding_deg: Annotated[float, Query(ge=0.0, le=2.0)] = 0.1,
    provider: Annotated[str, Query(description="Climate data provider key.")] = "era5-land",
) -> DistrictRasterClipRangeResponse:
    """Clip a range of months to a district's borders, one clip per month.

    This is the range version of /raster-clip: instead of a single
    year/month, give a start and end month (YYYY-MM) and get back one
    clipped GeoJSON result per month, in the same shape /raster-clip
    already returns for a single month.

    Months with no data are skipped rather than failing the whole
    request — you get back whatever months are actually available.
    """
    with request_context() as req_id:
        logger.info(
            "REQUEST_BEGIN endpoint=district_raster_clip_range request_id=%s "
            "district_id=%s variable=%s start=%s end=%s pid=%d thread_id=%d task_id=%s",
            req_id, district_id, variable, start, end,
            os.getpid(), threading.get_ident(), id(asyncio.current_task()),
        )
        flush()
        try:
            start_year, start_month = _parse_year_month(start, param_name="start")
            end_year, end_month = _parse_year_month(end, param_name="end")

            if (start_year, start_month) > (end_year, end_month):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="'start' must not be after 'end'.",
                )

            available = await computation.repository.get_available_range(
                provider=provider,
                variable=variable,
            )
            if available is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=_NO_PERIOD_MESSAGE,
                )
            min_year, min_month, max_year, max_month = available
            start_key = start_year * 12 + (start_month - 1)
            end_key = end_year * 12 + (end_month - 1)
            min_key = min_year * 12 + (min_month - 1)
            max_key = max_year * 12 + (max_month - 1)
            if start_key < min_key or end_key > max_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Selected month range falls outside the available "
                        f"dataset range {min_year:04d}-{min_month:02d} to "
                        f"{max_year:04d}-{max_month:02d}."
                    ),
                )

            results: list[DistrictRasterClipResponse] = []
            for year, month in _month_range(start_year, start_month, end_year, end_month):
                try:
                    clip_result = await clipper.clip(
                        district_id=district_id,
                        year=year,
                        month=month,
                        variable=variable,
                        padding_deg=padding_deg,
                        provider=provider,
                    )
                except DistrictNotFoundError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=str(exc),
                    )
                except KeyError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=str(exc),
                    )
                except ValueError as exc:
                    logger.info(
                        "REQUEST_ERROR endpoint=district_raster_clip_range "
                        "request_id=%s year=%04d month=%02d error=value_error "
                        "detail=%s skipping_month=true",
                        req_id, year, month, exc,
                    )
                    continue
                except FileNotFoundError as exc:
                    logger.error(
                        "REQUEST_ERROR endpoint=district_raster_clip_range "
                        "request_id=%s year=%04d month=%02d error=netcdf_missing "
                        "detail=%s skipping_month=true",
                        req_id, year, month, exc,
                    )
                    continue

                n_features = len(clip_result.feature_collection.get("features", []))
                if n_features > clipper.max_features:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            f"District yields {n_features} clipped cells for "
                            f"{year:04d}-{month:02d}, which exceeds the "
                            f"configured maximum of {clipper.max_features}. "
                            "Re-run with a smaller bbox padding or split the district."
                        ),
                    )

                results.append(DistrictRasterClipResponse.from_domain(clip_result))

            if not results:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No climate data available for any month between {start} and {end}.",
                )

            logger.info(
                "REQUEST_END endpoint=district_raster_clip_range request_id=%s "
                "district_id=%s months_processed=%d pid=%d thread_id=%d task_id=%s",
                req_id, district_id, len(results),
                os.getpid(), threading.get_ident(), id(asyncio.current_task()),
            )
            flush()
            return DistrictRasterClipRangeResponse(
                district_id=district_id,
                variable=variable,
                start=start,
                end=end,
                months_processed=len(results),
                results=results,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "REQUEST_ERROR endpoint=district_raster_clip_range request_id=%s "
                "error=unhandled pid=%d thread_id=%d task_id=%s",
                req_id, os.getpid(), threading.get_ident(), id(asyncio.current_task()),
            )
            flush()
            raise