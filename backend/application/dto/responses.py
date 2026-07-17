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
    """One month of raster statistics for a single district + variable."""

    year: int
    month: int
    mean: float
    min: float
    max: float


@dataclass
class DistrictRasterClipResponse:
    """End-to-end result of clipping one era5 variable/time-slice to a district."""

    district_id: str
    district_name: str
    state_id: str
    state_name: str
    variable: str
    variable_long_name: str
    nc_variable: str
    units: str
    year: int
    month: int
    time_decoded: str
    source_resolution_deg: float
    bbox_used: tuple[float, float, float, float]
    feature_collection: dict
    summary: dict
    diagnostics: dict
    asset_id: str
    asset_storage_key: str
    cache_hit: bool

    @classmethod
    def from_domain(cls, result) -> "DistrictRasterClipResponse":
        """Build the wire dto from a ``districtclipresult``."""
        meta = result.district_metadata
        return cls(
            district_id=meta.gid_2,
            district_name=meta.name_2,
            state_id=meta.gid_1,
            state_name=meta.name_1,
            variable=result.variable,
            variable_long_name=result.variable_long_name,
            nc_variable=result.nc_variable,
            units=result.units,
            year=result.year,
            month=result.month,
            time_decoded=result.time_decoded or "",
            source_resolution_deg=result.source_resolution_deg,
            bbox_used=result.bbox_used,
            feature_collection=result.feature_collection,
            summary=result.summary,
            diagnostics=result.diagnostics,
            asset_id=result.asset_id,
            asset_storage_key=result.asset_storage_key,
            cache_hit=result.cache_hit,
        )


@dataclass
class DistrictRasterClipRangeResponse:
    """Wire format for get /districts/{district_id}/raster-clip-range."""
    district_id: str
    variable: str
    start: str
    end: str
    months_processed: int
    results: list[DistrictRasterClipResponse]

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
