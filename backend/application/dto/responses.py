from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from domain.entities.climate_asset import ClimateAssetStatus


@dataclass
class ClimateAssetResponse:
    id: str
    provider: str
    variable: str
    year: int
    month: int
    storage_key: str
    checksum: str
    file_size: int
    status: ClimateAssetStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, asset) -> "ClimateAssetResponse":
        return cls(
            id=asset.id,
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


@dataclass
class StatisticsResponse:
    """Aggregated raster statistics over an inclusive month range."""

    district_id: str
    variable: str
    start_year: int
    start_month: int
    end_year: int
    end_month: int
    months_processed: int
    mean: float
    min: float
    max: float


@dataclass
class StateDistrictStatisticsItem:
    district_id: str
    mean: float
    min: float
    max: float


@dataclass
class StateDistrictStatisticsResponse:
    """Aggregated per-district statistics over an inclusive month range."""

    state_id: str
    variable: str
    start_year: int
    start_month: int
    end_year: int
    end_month: int
    months_processed: int
    districts: Sequence[StateDistrictStatisticsItem]


@dataclass
class MonthlySeriesPoint:
    """One month of raster statistics for a single district + variable.

    The fundamental time unit is ONE MONTH; the points are ordered
    ascending by ``(year, month)`` so the frontend can plot a clean
    chronological series without re-sorting.
    """

    year: int
    month: int
    mean: float
    min: float
    max: float


@dataclass
class DistrictMonthlySeriesResponse:
    """Per-month raster statistics for a district over an inclusive range."""

    district_id: str
    variable: str
    start_year: int
    start_month: int
    end_year: int
    end_month: int
    months_processed: int
    points: Sequence[MonthlySeriesPoint]
