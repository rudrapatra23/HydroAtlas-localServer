from __future__ import annotations

from typing import Sequence

from domain.entities.climate_asset import ClimateAsset
from domain.ports.dataset_repository import DatasetRepository


class DatasetService:
    """Service to fetch registered climate data."""

    def __init__(self, repository: DatasetRepository):
        self.repository = repository

    async def get_asset(self, asset_id: str) -> ClimateAsset | None:
        return await self.repository.get_by_id(asset_id)

    async def list_assets(self) -> Sequence[ClimateAsset]:
        return await self.repository.list()

    async def delete_asset(self, asset_id: str) -> None:
        """Deletes db record only, keeps s3 files intact."""
        await self.repository.delete(asset_id)
