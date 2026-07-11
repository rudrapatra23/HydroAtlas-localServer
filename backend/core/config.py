from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = Field(...)
    version: str = Field(...)
    environment: Environment = Field(...)
    log_level: LogLevel = Field(...)

    # AWS / S3
    aws_region: str = Field(...)
    aws_access_key_id: Optional[str] = Field(default=None)
    aws_secret_access_key: Optional[str] = Field(default=None)
    s3_bucket_name: str = Field(...)
    s3_endpoint_url: Optional[str] = Field(default=None)

    # Database
    database_url: str = Field(...)

    # ERA5 ingestion (optional — module is opt-in)
    cdsapi_url: Optional[str] = Field(default=None)
    cdsapi_key: Optional[str] = Field(default=None)

    era5_storage_root: Optional[Path] = Field(default=None)
    era5_logs_dir: Optional[Path] = Field(default=None)
    era5_s3_prefix: str = Field(default="era5-land")
    era5_dataset: str = Field(default="reanalysis-era5-land-monthly-means")
    era5_max_months: int = Field(default=480)
    era5_retry_attempts: int = Field(default=5)
    era5_retry_base_seconds: float = Field(default=2.0)
    era5_bootstrap_months: int = Field(default=24)
    era5_history_years: int = Field(default=10)
    era5_sync_concurrency: int = Field(default=4)
    era5_scheduler_timezone: str = Field(default="UTC")
    era5_scheduler_run_once: bool = Field(default=False)

    # District-level raster clipping (backend/district_clip/)
    # 0.1 deg padding = one ERA5-Land cell, matches validated prototype setting
    era5_district_clip_padding_deg: float = Field(default=0.1)
    # Safety cap on GeoJSON features per request; returns 422 if exceeded
    era5_district_clip_max_features: int = Field(default=10_000)

    # Raster acquisition cache — atime-based LRU, 0 disables on-disk cache
    raster_cache_max_bytes: int = Field(default=2 * 1024 * 1024 * 1024)

    def era5_storage_root_resolved(self) -> Path:
        if self.era5_storage_root is not None:
            return Path(self.era5_storage_root).resolve()
        return (Path(__file__).resolve().parent.parent / "data" / "era5").resolve()

    def era5_logs_dir_resolved(self) -> Path:
        if self.era5_logs_dir is not None:
            return Path(self.era5_logs_dir).resolve()
        return self.era5_storage_root_resolved() / "logs"

    def raster_cache_root_resolved(self) -> Path:
        return (self.era5_storage_root_resolved() / "cache").resolve()

    def cds_credentials_configured(self) -> bool:
        if not self.cdsapi_url or not self.cdsapi_key:
            return False
        if self.cdsapi_key == "replace-with-your-cds-api-key":
            return False
        return True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()