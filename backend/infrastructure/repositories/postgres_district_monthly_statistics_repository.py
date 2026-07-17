"""Postgresql repository for ``district_monthly_statistics``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.district_monthly_statistics_model import (
    DistrictMonthlyStatisticsModel,
)


@dataclass(frozen=True, slots=True)
class DistrictMonthlyStatisticsRow:
    """Plain dataclass mirror of :class:`districtmonthlystatisticsmodel`."""

    provider: str
    variable: str
    gid_2: str
    gid_1: str
    year: int
    month: int
    pixel_count: int
    valid_pixel_count: int
    valid_pixel_pct: Decimal
    mean: float
    minimum: float
    maximum: float
    source_asset_id: str
    bbox: Sequence[float]


def _from_row(row: DistrictMonthlyStatisticsRow) -> dict[str, object]:
    """Convert a dataclass row to the dict shape ``bulk_upsert`` needs."""
    return {
        "provider": row.provider,
        "variable": row.variable,
        "gid_2": row.gid_2,
        "gid_1": row.gid_1,
        "year": row.year,
        "month": row.month,
        "pixel_count": row.pixel_count,
        "valid_pixel_count": row.valid_pixel_count,
        "valid_pixel_pct": row.valid_pixel_pct,
        "mean": row.mean,
        "minimum": row.minimum,
        "maximum": row.maximum,
        "source_asset_id": row.source_asset_id,
        "bbox": list(row.bbox),
    }


def _to_domain(model: DistrictMonthlyStatisticsModel) -> DistrictMonthlyStatisticsRow:
    bbox = model.bbox if isinstance(model.bbox, list) else list(model.bbox)  # type: ignore[arg-type]
    return DistrictMonthlyStatisticsRow(
        provider=model.provider,
        variable=model.variable,
        gid_2=model.gid_2,
        gid_1=model.gid_1,
        year=model.year,
        month=model.month,
        pixel_count=model.pixel_count,
        valid_pixel_count=model.valid_pixel_count,
        valid_pixel_pct=model.valid_pixel_pct,
        mean=model.mean,
        minimum=model.minimum,
        maximum=model.maximum,
        source_asset_id=model.source_asset_id,
        bbox=tuple(bbox),
    )


class PostgresDistrictMonthlyStatisticsRepository:
    """Thin sqlalchemy wrapper around the new table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bulk_upsert(self, rows: Sequence[DistrictMonthlyStatisticsRow]) -> int:
        """Insert (or replace) every row in one statement."""
        if not rows:
            return 0
        payload = [_from_row(r) for r in rows]
        stmt = pg_insert(DistrictMonthlyStatisticsModel).values(payload)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_dms_provider_variable_gid_year_month",
            set_={
                "pixel_count": stmt.excluded.pixel_count,
                "valid_pixel_count": stmt.excluded.valid_pixel_count,
                "valid_pixel_pct": stmt.excluded.valid_pixel_pct,
                "mean": stmt.excluded.mean,
                "minimum": stmt.excluded.minimum,
                "maximum": stmt.excluded.maximum,
                "source_asset_id": stmt.excluded.source_asset_id,
                "bbox": stmt.excluded.bbox,
                "computed_at": func.now(),
            },
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        # ``rowcount`` reflects the number of rows the database touched
        # (inserted + updated); ``len(rows)`` is the upper bound.
        return int(result.rowcount or 0)

    async def count_for_asset(self, source_asset_id: str) -> int:
        """Number of precomputed rows that reference ``source_asset_id``."""
        stmt = select(func.count(DistrictMonthlyStatisticsModel.id)).where(
            DistrictMonthlyStatisticsModel.source_asset_id == source_asset_id,
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def get_for_district(
        self,
        *,
        provider: str,
        variable: str,
        gid_2: str,
        year: int,
        month: int,
    ) -> DistrictMonthlyStatisticsRow | None:
        """Return one precomputed row or ``none``."""
        stmt = select(DistrictMonthlyStatisticsModel).where(
            DistrictMonthlyStatisticsModel.provider == provider,
            DistrictMonthlyStatisticsModel.variable == variable,
            DistrictMonthlyStatisticsModel.gid_2 == gid_2,
            DistrictMonthlyStatisticsModel.year == year,
            DistrictMonthlyStatisticsModel.month == month,
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return _to_domain(model)

    async def delete_for_asset(self, source_asset_id: str) -> int:
        """Delete every row referencing ``source_asset_id``."""
        stmt = delete(DistrictMonthlyStatisticsModel).where(
            DistrictMonthlyStatisticsModel.source_asset_id == source_asset_id,
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return int(result.rowcount or 0)
