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

    # Core app bits
    app_name: str = Field(...)
    version: str = Field(...)
    environment: Environment = Field(...)
    log_level: LogLevel = Field(...)

    # AWS / S3 stuff
    aws_region: str = Field(...)
    aws_access_key_id: Optional[str] = Field(default=None)
    aws_secret_access_key: Optional[str] = Field(default=None)
    s3_bucket_name: str = Field(...)
    s3_endpoint_url: Optional[str] = Field(default=None)

    # Postgres connection string
    database_url: str = Field(...)

    # ERA5 ingestion knobs — the whole module is opt-in, so these
    # can stay unset unless you're running the ingestion pipeline
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

    # Settings for the district-level raster clipper (backend/district_clip/).
    # 0.1 deg padding == one ERA5-Land cell, which is what we landed on
    # during the prototype testing.
    era5_district_clip_padding_deg: float = Field(default=0.1)
    # Hard cap on how many GeoJSON features we'll clip in one go.
    # Anything bigger than this gets bounced back as a 422 to the caller.
    era5_district_clip_max_features: int = Field(default=10_000)

    # Size limit for the on-disk raster cache. It's an atime-based LRU,
    # so 0 here turns caching off entirely.
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