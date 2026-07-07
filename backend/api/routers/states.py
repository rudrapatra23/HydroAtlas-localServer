from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_repository, get_storage
from application.diagnostics import flush, request_context
from application.dto.requests import StatisticsRequest
from application.dto.responses import StateDistrictStatisticsResponse
from application.raster_computation import RasterComputation
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/states", tags=["states"])


async def get_raster_computation(
    repository: Annotated[DatasetRepository, Depends(get_repository)],
    storage: Annotated[StoragePort, Depends(get_storage)],
) -> RasterComputation:
    return RasterComputation(repository, storage)


_NO_PERIOD_MESSAGE = "No climate data available for the selected period."


def _validate_range_against_inventory(
    request: StatisticsRequest,
    available: tuple[int, int, int, int] | None,
) -> None:
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


@router.post("/{state_id}/districts/statistics", response_model=StateDistrictStatisticsResponse)
async def get_state_district_statistics(
    state_id: str,
    request: StatisticsRequest,
    computation: Annotated[RasterComputation, Depends(get_raster_computation)],
) -> StateDistrictStatisticsResponse:
    """Get aggregated per-district raster statistics for a state over a month range."""
    with request_context() as req_id:
        t_req = time.perf_counter()
        logger.info(
            "REQUEST_BEGIN endpoint=state_district_statistics request_id=%s "
            "state_id=%s variable=%s start=%04d-%02d end=%04d-%02d "
            "pid=%d thread_id=%d task_id=%s",
            req_id, state_id, request.variable,
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
                    "REQUEST_ERROR endpoint=state_district_statistics request_id=%s "
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
                months_processed, districts = await computation.compute_for_state_range(
                    state_gid=state_id,
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
                        "REQUEST_ERROR endpoint=state_district_statistics request_id=%s "
                        "error=no_period pid=%d thread_id=%d",
                        req_id, os.getpid(), threading.get_ident(),
                    )
                    flush()
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=_NO_PERIOD_MESSAGE,
                    )
                logger.info(
                    "REQUEST_ERROR endpoint=state_district_statistics request_id=%s "
                    "error=value_error detail=%s pid=%d thread_id=%d",
                    req_id, message, os.getpid(), threading.get_ident(),
                )
                flush()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=message,
                )

            t_ser = time.perf_counter()
            response = StateDistrictStatisticsResponse(
                state_id=state_id,
                variable=request.variable,
                start_year=request.start_year,
                start_month=request.start_month,
                end_year=request.end_year,
                end_month=request.end_month,
                months_processed=months_processed,
                districts=districts,
            )
            t_end = time.perf_counter()
            logger.info("JSON serialization/response creation: %.3fs", t_end - t_ser)
            logger.info("Total request time: %.3fs", t_end - t_req)
            logger.info(
                "REQUEST_END endpoint=state_district_statistics request_id=%s "
                "state_id=%s months_processed=%d districts=%d "
                "pid=%d thread_id=%d task_id=%s",
                req_id, state_id, months_processed, len(districts),
                os.getpid(), threading.get_ident(), id(asyncio.current_task()),
            )
            flush()
            return response
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "REQUEST_ERROR endpoint=state_district_statistics request_id=%s "
                "error=unhandled pid=%d thread_id=%d task_id=%s",
                req_id, os.getpid(), threading.get_ident(), id(asyncio.current_task()),
            )
            flush()
            raise
