from __future__ import annotations

import logging
import uuid
from typing import Optional, Sequence

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
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


class PostgresDatasetRepository(DatasetRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, asset: ClimateAsset) -> ClimateAsset:
        asset_id = asset.id or str(uuid.uuid4())
        payload = {
            "id": asset_id,
            "provider": asset.provider,
            "variable": asset.variable,
            "year": asset.year,
            "month": asset.month,
            "storage_key": asset.storage_key,
            "checksum": asset.checksum,
            "file_size": asset.file_size,
            "status": asset.status,
            "created_at": asset.created_at,
            "updated_at": asset.updated_at,
        }
        stmt = pg_insert(ClimateAssetModel).values(payload)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_climate_assets_provider_variable_year_month",
            set_={
                "storage_key": stmt.excluded.storage_key,
                "checksum": stmt.excluded.checksum,
                "file_size": stmt.excluded.file_size,
                "status": stmt.excluded.status,
                "created_at": stmt.excluded.created_at,
                "updated_at": stmt.excluded.updated_at,
            },
        ).returning(ClimateAssetModel.id)
        result = await self.session.execute(stmt)
        await self.session.commit()
        asset_id = result.scalar_one()
        model = await self.session.get(ClimateAssetModel, asset_id)
        if model is None:
            raise RuntimeError(
                f"Upserted climate asset {asset_id} but failed to reload it"
            )
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
        """Return ``(min_year, min_month, max_year, max_month)`` in a."""
        stmt = select(
            func.min(ClimateAssetModel.year),
            func.min(ClimateAssetModel.month),
            func.max(ClimateAssetModel.year),
            func.max(ClimateAssetModel.month),
        ).where(
            ClimateAssetModel.provider == provider,
            ClimateAssetModel.variable == variable,
        )
        result = await self.session.execute(stmt)
        row = result.one()
        min_year, min_month, max_year, max_month = row
        if min_year is None or max_year is None:
            return None
        # ``min(month)`` may not co-locate with ``min(year)`` when the
        # earliest year only contains later months, so resolve the true
        # boundaries with a second pass that joins on the year extremes.
        if min_year == max_year:
            return (int(min_year), int(min_month), int(max_year), int(max_month))

        earliest_stmt = select(ClimateAssetModel.month).where(
            ClimateAssetModel.provider == provider,
            ClimateAssetModel.variable == variable,
            ClimateAssetModel.year == min_year,
        )
        latest_stmt = select(ClimateAssetModel.month).where(
            ClimateAssetModel.provider == provider,
            ClimateAssetModel.variable == variable,
            ClimateAssetModel.year == max_year,
        )
        earliest_months = (await self.session.execute(earliest_stmt)).scalars().all()
        latest_months = (await self.session.execute(latest_stmt)).scalars().all()
        return (
            int(min_year),
            int(min(earliest_months)),
            int(max_year),
            int(max(latest_months)),
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
