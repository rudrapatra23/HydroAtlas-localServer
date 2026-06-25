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
    def get_by_period(self, year: int, month: int, provider: str, variable: Optional[str] = None) -> ClimateAsset | None:
        raise NotImplementedError

    @abstractmethod
    def list(self) -> Sequence[ClimateAsset]:
        raise NotImplementedError

    @abstractmethod
    def delete(self, asset_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def exists(self, provider: str, variable: str, year: int, month: int) -> bool:
        raise NotImplementedError
