"""PostgreSQL repository for ``district_monthly_statistics``.

Write methods are what the precompute service uses; read methods are
included so callers can query the table directly without going through
any router. The read methods match the index choices in the migration:
range queries hit the unique ``(provider, variable, gid_2, year, month)``
index or the secondary ``(provider, variable, gid_1, year, month)`` index.
"""

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
    """Plain dataclass mirror of :class:`DistrictMonthlyStatisticsModel`.

    Frozen so the precompute service cannot accidentally mutate a row
    after it has been handed off to ``bulk_upsert``. ``computed_at`` is
    left to the database default (``now()``) and is not accepted from
    the caller.
    """

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
    """Convert a dataclass row to the dict shape ``bulk_upsert`` needs.

    ``bbox`` is stored as a list so PostgreSQL's JSONB encoder can
    serialise it; the dataclass keeps it as ``Sequence[float]`` to
    prevent callers from passing a dict.
    """
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
    """Thin SQLAlchemy wrapper around the new table.

    Constructor signature mirrors
    :class:`PostgresDatasetRepository` so the dependency-injection layer
    in ``api/dependencies.py`` can build both repositories from the same
    async session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bulk_upsert(self, rows: Sequence[DistrictMonthlyStatisticsRow]) -> int:
        """Insert (or replace) every row in one statement.

        Uses the PostgreSQL ``ON CONFLICT`` clause keyed on the unique
        constraint to keep recomputation idempotent. Returns the count
        of rows the database actually wrote — equal to ``len(rows)`` on
        a clean run and ``0 < n < len(rows)`` on a re-run that
        conflicts on every existing key.
        """
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
        """Number of precomputed rows that reference ``source_asset_id``.

        Used by the precompute command's "already done?" short-circuit
        and by the GC helper to detect dangling references.
        """
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
        """Return one precomputed row or ``None``.

        Backed by the unique constraint — a single index seek.
        """
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
        """Delete every row referencing ``source_asset_id``.

        Useful when an asset is re-uploaded with a new id; the
        precompute command can call this before re-running. Returns the
        number of rows deleted.
        """
        stmt = delete(DistrictMonthlyStatisticsModel).where(
            DistrictMonthlyStatisticsModel.source_asset_id == source_asset_id,
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return int(result.rowcount or 0)
