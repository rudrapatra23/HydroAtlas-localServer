from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_repository, get_storage
from application.dto.requests import StatisticsRequest
from application.dto.responses import StatisticsResponse
from application.raster_computation import RasterComputation
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort

router = APIRouter(prefix="/districts", tags=["districts"])


async def get_raster_computation(
    repository: Annotated[DatasetRepository, Depends(get_repository)],
    storage: Annotated[StoragePort, Depends(get_storage)],
) -> RasterComputation:
    return RasterComputation(repository, storage)


@router.post("/{district_id}/statistics", response_model=StatisticsResponse)
async def get_district_statistics(
    district_id: str,
    request: StatisticsRequest,
    computation: Annotated[RasterComputation, Depends(get_raster_computation)],
) -> StatisticsResponse:
    """Get raster statistics for a district."""
    try:
        result = await computation.compute_for_district(
            district_gid=district_id,
            year=request.year,
            month=request.month,
            variable=request.variable,
        )
        return StatisticsResponse(
            district_id=district_id,
            variable=request.variable,
            mean=result.mean,
            min=result.minimum,
            max=result.maximum,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
