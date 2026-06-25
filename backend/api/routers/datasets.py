from __future__ import annotations

from typing import Annotated, Sequence

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_dataset_service
from application.dataset_service import DatasetService
from application.dto.requests import DownloadRequest
from application.dto.responses import ClimateAssetResponse, DownloadResponse

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.post("/download", response_model=DownloadResponse)
async def download_dataset(
    request: DownloadRequest,
    service: Annotated[DatasetService, Depends(get_dataset_service)],
) -> DownloadResponse:
    """Download and register a climate dataset."""
    return await service.download_and_register(request)


@router.get("", response_model=Sequence[ClimateAssetResponse])
async def list_datasets(
    service: Annotated[DatasetService, Depends(get_dataset_service)],
) -> Sequence[ClimateAssetResponse]:
    """List all registered climate assets."""
    assets = await service.list_assets()
    return [ClimateAssetResponse.from_domain(asset) for asset in assets]


@router.get("/{id}", response_model=ClimateAssetResponse)
async def get_dataset(
    id: str,
    service: Annotated[DatasetService, Depends(get_dataset_service)],
) -> ClimateAssetResponse:
    """Get a single climate asset by ID."""
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
    """Delete a climate asset by ID."""
    asset = await service.get_asset(id)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Climate asset not found",
        )
    await service.delete_asset(id)
