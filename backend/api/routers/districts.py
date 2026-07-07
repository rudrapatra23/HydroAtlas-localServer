from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_repository, get_storage
from application.diagnostics import flush, request_context
from application.dto.requests import StatisticsRequest
from application.dto.responses import DistrictMonthlySeriesResponse, StatisticsResponse
from application.raster_computation import RasterComputation
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/districts", tags=["districts"])


async def get_raster_computation(
    repository: Annotated[DatasetRepository, Depends(get_repository)],
    storage: Annotated[StoragePort, Depends(get_storage)],
) -> RasterComputation:
    return RasterComputation(repository, storage)


# The fundamental time unit is one month. The frontend sends an inclusive
# ``[start, end]`` month range; the backend uses PostgreSQL
# ``climate_assets`` as the index to find every monthly asset between the
# two months, downloads them from S3 sequentially, and returns aggregated
# statistics over the period.
_NO_PERIOD_MESSAGE = "No climate data available for the selected period."


def _validate_range_against_inventory(
    request: StatisticsRequest,
    available: tuple[int, int, int, int] | None,
) -> None:
    """Reject requests whose month range falls outside the known inventory.

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
    """Get aggregated raster statistics for a district over a month range."""
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
    """Get per-month raster statistics for a district over a month range.

    The response is the natural primitive for the BottomPanel's Time
    Series, Trend, and Export tabs — each ``point`` carries the
    ``(year, month)`` anchor plus ``mean``/``min``/``max`` so the
    frontend can plot a clean chronological series, compute a regression
    line, or build a CSV without further round-trips.
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
