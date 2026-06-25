from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from domain.entities.climate_asset import ClimateAssetStatus


@dataclass
class DownloadResponse:
    success: bool
    file_path: Optional[Path] = None
    checksum: Optional[str] = None
    file_size: Optional[int] = None
    error_message: Optional[str] = None


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
