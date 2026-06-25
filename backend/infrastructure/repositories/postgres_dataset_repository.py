from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from domain.ports.dataset_repository import DatasetRepository
from infrastructure.db.climate_asset_model import ClimateAssetModel


def _to_domain(model: ClimateAssetModel) -> ClimateAsset:
    return ClimateAsset(
        id=model.id,
        provider=model.provider,
        variable=model.variable,
        year=model.year,
        month=model.month,
        storage_key=model.storage_key,
        checksum=model.checksum,
        file_size=model.file_size,
        status=model.status,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _from_domain(domain: ClimateAsset) -> ClimateAssetModel:
    return ClimateAssetModel(
        id=domain.id,
        provider=domain.provider,
        variable=domain.variable,
        year=domain.year,
        month=domain.month,
        storage_key=domain.storage_key,
        checksum=domain.checksum,
        file_size=domain.file_size,
        status=domain.status,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
    )


class PostgresDatasetRepository(DatasetRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, asset: ClimateAsset) -> ClimateAsset:
        if asset.id is None:
            asset = ClimateAsset(
                id=str(uuid.uuid4()),
                provider=asset.provider,
                variable=asset.variable,
                year=asset.year,
                month=asset.month,
                storage_key=asset.storage_key,
                checksum=asset.checksum,
                file_size=asset.file_size,
                status=asset.status,
                created_at=asset.created_at,
                updated_at=asset.updated_at,
            )

        model = _from_domain(asset)
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return _to_domain(model)

    async def get_by_id(self, asset_id: str) -> ClimateAsset | None:
        stmt = select(ClimateAssetModel).where(ClimateAssetModel.id == asset_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return _to_domain(model)

    async def get_by_period(self, year: int, month: int, provider: str, variable: Optional[str] = None) -> ClimateAsset | None:
        stmt = select(ClimateAssetModel).where(
            ClimateAssetModel.provider == provider,
            ClimateAssetModel.year == year,
            ClimateAssetModel.month == month,
        )
        if variable is not None:
            stmt = stmt.where(ClimateAssetModel.variable == variable)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return _to_domain(model)

    async def list(self) -> Sequence[ClimateAsset]:
        stmt = select(ClimateAssetModel).order_by(ClimateAssetModel.created_at.desc())
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [_to_domain(m) for m in models]

    async def delete(self, asset_id: str) -> None:
        stmt = select(ClimateAssetModel).where(ClimateAssetModel.id == asset_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is not None:
            await self.session.delete(model)
            await self.session.commit()

    async def exists(self, provider: str, variable: str, year: int, month: int) -> bool:
        stmt = select(ClimateAssetModel.id).where(
            ClimateAssetModel.provider == provider,
            ClimateAssetModel.variable == variable,
            ClimateAssetModel.year == year,
            ClimateAssetModel.month == month,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None
