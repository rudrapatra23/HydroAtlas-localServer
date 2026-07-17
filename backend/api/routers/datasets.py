from __future__ import annotations

from typing import Annotated, Sequence

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_dataset_service
from application.dataset_service import DatasetService
from application.dto.responses import ClimateAssetResponse

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("", response_model=Sequence[ClimateAssetResponse])
async def list_datasets(
    service: Annotated[DatasetService, Depends(get_dataset_service)],
) -> Sequence[ClimateAssetResponse]:
    """Show a list of all the climate data files we have registered in the system."""
    assets = await service.list_assets()
    return [ClimateAssetResponse.from_domain(asset) for asset in assets]


@router.get("/{id}", response_model=ClimateAssetResponse)
async def get_dataset(
    id: str,
    service: Annotated[DatasetService, Depends(get_dataset_service)],
) -> ClimateAssetResponse:
    """Look up the details for a specific data file using its ID."""
    asset = await service.get_asset(id)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Climate asset not found",
        )
    return ClimateAssetResponse.from_domain(asset)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    id: str,
    service: Annotated[DatasetService, Depends(get_dataset_service)],
) -> None:
    """Remove the record of a climate data file from our database."""
    asset = await service.get_asset(id)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Climate asset not found",
        )
    await service.delete_asset(id)
