from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import xarray as xr


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class SpatialExtent:
    min_latitude: float
    min_longitude: float
    max_latitude: float
    max_longitude: float


@dataclass(frozen=True, slots=True)
class ClimateMetadata:
    dataset_id: str
    variables: tuple[str, ...]
    spatial_extent: SpatialExtent
    temporal_extent: TimeRange
    crs: str
    resolution_degrees: float | None
    chunk_sizes: dict[str, int]


class ClimateRepository(ABC):
    @abstractmethod
    def get_metadata(self) -> ClimateMetadata:
        pass

    @abstractmethod
    def get_timeseries(
        self,
        variable: str,
        latitude: float,
        longitude: float,
        time_range: TimeRange | None = None,
    ) -> "xr.DataArray":
        pass

    @abstractmethod
    def get_grid(
        self,
        variable: str,
        time: datetime,
    ) -> "xr.Dataset":
        pass


