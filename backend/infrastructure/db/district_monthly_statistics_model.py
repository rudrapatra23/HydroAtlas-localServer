"""Orm model for ``district_monthly_statistics``."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from infrastructure.db.climate_asset_model import Base


class DistrictMonthlyStatisticsModel(Base):
    """One row per ``(provider, variable, gid_2, year, month)``."""

    __tablename__ = "district_monthly_statistics"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "variable",
            "gid_2",
            "year",
            "month",
            name="uq_dms_provider_variable_gid_year_month",
        ),
        CheckConstraint("year BETWEEN 1900 AND 2100", name="ck_dms_year"),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_dms_month"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    variable: Mapped[str] = mapped_column(String(64), nullable=False)
    gid_2: Mapped[str] = mapped_column(String(64), nullable=False)
    gid_1: Mapped[str] = mapped_column(String(64), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    pixel_count: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_pixel_count: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_pixel_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    mean: Mapped[float] = mapped_column(Float, nullable=False)
    minimum: Mapped[float] = mapped_column(Float, nullable=False)
    maximum: Mapped[float] = mapped_column(Float, nullable=False)
    source_asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    bbox: Mapped[Any] = mapped_column(JSON, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=None,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<DistrictMonthlyStatisticsModel("
            f"provider={self.provider!r}, variable={self.variable!r}, "
            f"gid_2={self.gid_2!r}, year={self.year}, month={self.month})>"
        )


# Re-export the declarative base so other modules can ``from
# infrastructure.db.district_monthly_statistics_model import Base`` if
# they need to register against the same metadata.
__all__ = ["Base", "DistrictMonthlyStatisticsModel"]
