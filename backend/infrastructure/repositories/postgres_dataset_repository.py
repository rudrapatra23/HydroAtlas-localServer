from __future__ import annotations

import logging
import uuid
from typing import Sequence

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from domain.ports.dataset_repository import DatasetRepository
from infrastructure.db.climate_asset_model import ClimateAssetModel

logger = logging.getLogger("uvicorn.error")


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


class SqlAlchemyDatasetRepository(DatasetRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, asset: ClimateAsset) -> ClimateAsset:
        existing = await self.get_by_period(
            asset.year,
            asset.month,
            asset.provider,
            asset.variable,
        )
        if existing is not None:
            model = await self.session.get(ClimateAssetModel, existing.id)
            if model is None:
                raise RuntimeError(
                    f"Found asset {existing.id} during upsert but could not reload it"
                )
            model.storage_key = asset.storage_key
            model.checksum = asset.checksum
            model.file_size = asset.file_size
            model.status = asset.status
            model.created_at = asset.created_at
            model.updated_at = asset.updated_at
        else:
            model = _from_domain(
                ClimateAsset(
                    id=asset.id or str(uuid.uuid4()),
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
            )
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

    async def get_by_period(self, year: int, month: int, provider: str, variable: str) -> ClimateAsset | None:
        stmt = (
            select(ClimateAssetModel)
            .where(
                ClimateAssetModel.provider == provider,
                ClimateAssetModel.variable == variable,
                ClimateAssetModel.year == year,
                ClimateAssetModel.month == month,
            )
            .order_by(
                ClimateAssetModel.created_at.desc(),
                ClimateAssetModel.id.desc(),
            )
            .limit(2)
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        if not models:
            return None
        if len(models) > 1:
            logger.warning(
                "climate_assets duplicate rows found for provider=%s variable=%s year=%04d month=%02d; using latest created_at row",
                provider,
                variable,
                year,
                month,
            )
        return _to_domain(models[0])

    async def list_by_period_range(
        self,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
        provider: str,
        variable: str,
    ) -> Sequence[ClimateAsset]:
        """Return every asset in the inclusive ``[start, end]`` month range."""
        period_column = tuple_(ClimateAssetModel.year, ClimateAssetModel.month)
        stmt = (
            select(ClimateAssetModel)
            .where(
                ClimateAssetModel.provider == provider,
                ClimateAssetModel.variable == variable,
                period_column >= (start_year, start_month),
                period_column <= (end_year, end_month),
            )
            .order_by(
                ClimateAssetModel.year.asc(),
                ClimateAssetModel.month.asc(),
                ClimateAssetModel.created_at.desc(),
                ClimateAssetModel.id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        deduped: list[ClimateAssetModel] = []
        seen_periods: set[tuple[int, int]] = set()
        dropped_duplicates = 0
        for model in models:
            period = (model.year, model.month)
            if period in seen_periods:
                dropped_duplicates += 1
                continue
            seen_periods.add(period)
            deduped.append(model)
        if dropped_duplicates:
            logger.warning(
                "climate_assets duplicate rows found for provider=%s variable=%s range=%04d-%02d..%04d-%02d; dropped %d stale rows",
                provider,
                variable,
                start_year,
                start_month,
                end_year,
                end_month,
                dropped_duplicates,
            )
        return [_to_domain(m) for m in deduped]

    async def list(self) -> Sequence[ClimateAsset]:
        stmt = select(ClimateAssetModel).order_by(ClimateAssetModel.created_at.desc())
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [_to_domain(m) for m in models]

    async def get_available_range(
        self, provider: str, variable: str
    ) -> tuple[int, int, int, int] | None:
        earliest_stmt = (
            select(ClimateAssetModel.year, ClimateAssetModel.month)
            .where(
                ClimateAssetModel.provider == provider,
                ClimateAssetModel.variable == variable,
            )
            .order_by(ClimateAssetModel.year.asc(), ClimateAssetModel.month.asc())
            .limit(1)
        )
        latest_stmt = (
            select(ClimateAssetModel.year, ClimateAssetModel.month)
            .where(
                ClimateAssetModel.provider == provider,
                ClimateAssetModel.variable == variable,
            )
            .order_by(ClimateAssetModel.year.desc(), ClimateAssetModel.month.desc())
            .limit(1)
        )
        earliest = (await self.session.execute(earliest_stmt)).one_or_none()
        latest = (await self.session.execute(latest_stmt)).one_or_none()
        if earliest is None or latest is None:
            return None
        return (
            int(earliest[0]),
            int(earliest[1]),
            int(latest[0]),
            int(latest[1]),
        )

    async def delete(self, asset_id: str) -> None:
        stmt = select(ClimateAssetModel).where(ClimateAssetModel.id == asset_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is not None:
            await self.session.delete(model)
            await self.session.commit()

    async def exists(self, provider: str, variable: str, year: int, month: int) -> bool:
        stmt = (
            select(ClimateAssetModel.id)
            .where(
                ClimateAssetModel.provider == provider,
                ClimateAssetModel.variable == variable,
                ClimateAssetModel.year == year,
                ClimateAssetModel.month == month,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar() is not None


PostgresDatasetRepository = SqlAlchemyDatasetRepository
