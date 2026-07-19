from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core app bits
    app_name: str = Field(default="HydroAtlas")
    version: str = Field(default="1.0.0")
    environment: Environment = Field(default="development")
    log_level: LogLevel = Field(default="INFO")

    # Local SQLite database used by both the API and Alembic.
    database_url: str = Field(default="sqlite+aiosqlite:///./hydroatlas.db")

    # ERA5 ingestion knobs — the whole module is opt-in, so these
    # can stay unset unless you're running the ingestion pipeline
    cdsapi_url: Optional[str] = Field(default=None)
    cdsapi_key: Optional[str] = Field(default=None)

    storage_root: Optional[Path] = Field(default=None)
    era5_storage_root: Optional[Path] = Field(default=None)
    era5_logs_dir: Optional[Path] = Field(default=None)
    era5_storage_prefix: str = Field(default="era5")
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

    @model_validator(mode="after")
    def normalize_local_runtime(self) -> "Settings":
        db_url = self.database_url.strip()
        if db_url.startswith("sqlite:///") and not db_url.startswith("sqlite+aiosqlite:///"):
            self.database_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        elif not db_url.startswith("sqlite+aiosqlite:///"):
            self.database_url = "sqlite+aiosqlite:///./hydroatlas.db"
        return self

    def repo_root_resolved(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def storage_root_resolved(self) -> Path:
        if self.storage_root is not None:
            return Path(self.storage_root).resolve()
        return (self.repo_root_resolved() / "storage").resolve()

    def era5_storage_root_resolved(self) -> Path:
        if self.era5_storage_root is not None:
            return Path(self.era5_storage_root).resolve()
        return (self.storage_root_resolved() / "era5").resolve()

    def era5_logs_dir_resolved(self) -> Path:
        if self.era5_logs_dir is not None:
            return Path(self.era5_logs_dir).resolve()
        return (self.storage_root_resolved() / "cache" / "logs").resolve()

    def raster_cache_root_resolved(self) -> Path:
        return (self.storage_root_resolved() / "cache").resolve()

    def cds_credentials_configured(self) -> bool:
        if not self.cdsapi_url or not self.cdsapi_key:
            return False
        if self.cdsapi_key == "replace-with-your-cds-api-key":
            return False
        return True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
