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

    # ── Application
    app_name: str = Field(...)
    version: str = Field(...)
    environment: Environment = Field(...)
    log_level: LogLevel = Field(...)

    # ── AWS / S3
    aws_region: str = Field(...)
    aws_access_key_id: str = Field(...)
    aws_secret_access_key: str = Field(...)
    s3_bucket_name: str = Field(...)
    s3_endpoint_url: str = Field(...)

    # ── Database
    database_url: str = Field(...)

    # ── ERA5 ingestion (optional — module is opt-in)
    # Copernicus Climate Data Store credentials. Required for ERA5 downloads.
    cdsapi_url: Optional[str] = Field(default=None)
    cdsapi_key: Optional[str] = Field(default=None)

    # Local cache root for downloaded NetCDF bundles.
    # Defaults to <repo>/backend/data/era5 if unset.
    era5_storage_root: Optional[Path] = Field(default=None)
    era5_logs_dir: Optional[Path] = Field(default=None)

    # S3 key prefix for published per-variable assets.
    era5_s3_prefix: str = Field(default="era5-land")

    # CDS dataset name (reanalysis-era5-land-monthly-means is the hydrology source).
    era5_dataset: str = Field(default="reanalysis-era5-land-monthly-means")

    # Cache + retry knobs.
    era5_max_months: int = Field(default=480)
    era5_retry_attempts: int = Field(default=5)
    era5_retry_base_seconds: float = Field(default=2.0)
    era5_bootstrap_months: int = Field(default=24)

    # ── District-level raster clipping (backend/district_clip/)
    # Bbox padding in degrees added to the district polygon before the
    # Stage-2 NetCDF read.  0.1 = one ERA5-Land cell, which exactly
    # matches the prototype's validated setting; raising it slightly
    # (~0.15) gives extra margin at the cost of a few more bbox cells.
    era5_district_clip_padding_deg: float = Field(default=0.1)
    # Hard cap on the number of GeoJSON features the new endpoint will
    # emit per request.  Acts as a payload-size safety net so a runaway
    # request (e.g. very large maritime district) cannot OOM the
    # process.  The endpoint never silently truncates; when the cap is
    # hit it returns a 422 with a clear message.
    era5_district_clip_max_features: int = Field(default=10_000)

    # ── Raster acquisition cache
    # Bounded disk cache for runtime raster acquisitions. The atime-based
    # LRU sweep evicts oldest files first when over budget; files held by
    # outstanding leases are never evicted. ``0`` disables the on-disk
    # cache entirely (every acquire downloads to a tempfile managed by
    # the lease lifecycle). Default 2 GiB.
    raster_cache_max_bytes: int = Field(default=2 * 1024 * 1024 * 1024)

    def era5_storage_root_resolved(self) -> Path:
        """Resolve the ERA5 storage root, defaulting to backend/data/era5."""
        if self.era5_storage_root is not None:
            return Path(self.era5_storage_root).resolve()
        # backend/core/config.py -> backend/data/era5
        return (Path(__file__).resolve().parent.parent / "data" / "era5").resolve()

    def era5_logs_dir_resolved(self) -> Path:
        """Resolve the ERA5 logs directory, defaulting to backend/data/era5/logs."""
        if self.era5_logs_dir is not None:
            return Path(self.era5_logs_dir).resolve()
        return self.era5_storage_root_resolved() / "logs"

    def raster_cache_root_resolved(self) -> Path:
        """Resolve the canonical runtime raster cache root.

        The runtime acquisition cache is intentionally colocated under the
        ERA5 storage root so ingestion and on-demand reads agree on a single
        cache identity regardless of the process working directory.
        """
        return (self.era5_storage_root_resolved() / "cache").resolve()

    def cds_credentials_configured(self) -> bool:
        """True if CDSAPI_URL and CDSAPI_KEY are both set to non-placeholder values."""
        if not self.cdsapi_url or not self.cdsapi_key:
            return False
        if self.cdsapi_key == "replace-with-your-cds-api-key":
            return False
        return True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
