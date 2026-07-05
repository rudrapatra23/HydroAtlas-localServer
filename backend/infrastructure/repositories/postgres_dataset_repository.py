from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import func, select, tuple_
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

    async def get_by_period(self, year: int, month: int, provider: str, variable: str) -> ClimateAsset | None:
        stmt = select(ClimateAssetModel).where(
            ClimateAssetModel.provider == provider,
            ClimateAssetModel.variable == variable,
            ClimateAssetModel.year == year,
            ClimateAssetModel.month == month,
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return _to_domain(model)

    async def list_by_period_range(
        self,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
        provider: str,
        variable: str,
    ) -> Sequence[ClimateAsset]:
        """Return every asset in the inclusive ``[start, end]`` month range.

        Uses a tuple comparison on ``(year, month)`` so the database can
        satisfy the range with a single index scan. Results are ordered
        ascending so the caller iterates the time series sequentially.
        """
        period_column = tuple_(ClimateAssetModel.year, ClimateAssetModel.month)
        stmt = (
            select(ClimateAssetModel)
            .where(
                ClimateAssetModel.provider == provider,
                ClimateAssetModel.variable == variable,
                period_column >= (start_year, start_month),
                period_column <= (end_year, end_month),
            )
            .order_by(ClimateAssetModel.year.asc(), ClimateAssetModel.month.asc())
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [_to_domain(m) for m in models]

    async def list(self) -> Sequence[ClimateAsset]:
        stmt = select(ClimateAssetModel).order_by(ClimateAssetModel.created_at.desc())
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [_to_domain(m) for m in models]

    async def get_available_range(
        self, provider: str, variable: str
    ) -> tuple[int, int, int, int] | None:
        """Return ``(min_year, min_month, max_year, max_month)`` in a
        single aggregate query so the router can validate incoming
        month-range requests without scanning every asset row.
        """
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
        stmt = select(ClimateAssetModel.id).where(
            ClimateAssetModel.provider == provider,
            ClimateAssetModel.variable == variable,
            ClimateAssetModel.year == year,
            ClimateAssetModel.month == month,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None
