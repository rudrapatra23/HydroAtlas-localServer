from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from domain.entities.climate_asset import ClimateAsset


class DatasetRepository(ABC):
    @abstractmethod
    def save(self, asset: ClimateAsset) -> ClimateAsset:
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, asset_id: str) -> ClimateAsset | None:
        raise NotImplementedError

    @abstractmethod
    def get_by_period(self, year: int, month: int, provider: str, variable: str) -> ClimateAsset | None:
        raise NotImplementedError

    @abstractmethod
    def list_by_period_range(
        self,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
        provider: str,
        variable: str,
    ) -> Sequence[ClimateAsset]:
        """Return every asset inside the inclusive ``[start, end]`` month range."""
        raise NotImplementedError

    @abstractmethod
    def list(self) -> Sequence[ClimateAsset]:
        raise NotImplementedError

    @abstractmethod
    def get_available_range(
        self, provider: str, variable: str
    ) -> tuple[int, int, int, int] | None:
        """Return ``(min_year, min_month, max_year, max_month)`` for the."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, asset_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def exists(self, provider: str, variable: str, year: int, month: int) -> bool:
        raise NotImplementedError
