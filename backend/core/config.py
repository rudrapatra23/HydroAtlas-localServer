"""Application configuration loaded from environment variables.

This module is the single source of truth for runtime configuration.
Every setting is required; the process fails to start if any variable
is missing or has an invalid type. No fallback values are permitted.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Populate os.environ from a local .env file (if present) before the
# Settings class is instantiated. Module-level execution runs exactly
# once at process startup, which satisfies the "load_dotenv() at
# startup" requirement without polluting other modules.
load_dotenv()

# Closed sets of valid string values. Using Literal forces Pydantic to
# reject any value outside the allowed set, satisfying the "fail
# immediately if invalid" contract.
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """Strongly-typed application settings.

    All fields are required. Pydantic raises a ValidationError at
    instantiation time when a variable is absent or malformed, which
    causes the FastAPI app factory to abort before serving traffic.
    """

    # Pydantic Settings configuration block.
    # - env_file: tells BaseSettings to also read from a .env file
    # - env_file_encoding: explicit UTF-8 decoding for the .env file
    # - case_sensitive: env variable name matching is case-insensitive
    # - extra: unknown env variables are ignored rather than rejected
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application metadata -----------------------------------------
    app_name: str = Field(
        ...,
        description="Human-readable application name.",
    )
    version: str = Field(
        ...,
        description="Semantic version of the deployed build.",
    )
    environment: Environment = Field(
        ...,
        description="Deployment environment identifier.",
    )

    # --- AWS / S3 configuration ---------------------------------------
    # These back the s3fs + Zarr data access path described in
    # ARCHITECTURE.md. They are required because the project must not
    # silently default to a local filesystem.
    aws_region: str = Field(
        ...,
        description="AWS region hosting the S3 bucket.",
    )
    aws_access_key_id: str = Field(
        ...,
        description="AWS access key ID for S3 authentication.",
    )
    aws_secret_access_key: str = Field(
        ...,
        description="AWS secret access key for S3 authentication.",
    )
    s3_bucket_name: str = Field(
        ...,
        description="Name of the S3 bucket containing Zarr datasets.",
    )
    s3_endpoint_url: str = Field(
        ...,
        description="S3 endpoint URL (regional or custom).",
    )

    # --- Logging ------------------------------------------------------
    log_level: LogLevel = Field(
        ...,
        description="Application log verbosity.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance.

    Caching guarantees:
      1. Environment parsing and validation run exactly once per
         process, keeping startup cost predictable.
      2. Every caller receives the same immutable configuration
         object, preventing accidental divergence between layers.

    The cache is process-local; tests can clear it via
    ``get_settings.cache_clear()`` if isolation is required.
    """
    return Settings()
