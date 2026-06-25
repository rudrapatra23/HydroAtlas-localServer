from __future__ import annotations

from functools import lru_cache
from typing import Literal

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

    app_name: str = Field(...)
    version: str = Field(...)
    environment: Environment = Field(...)

    aws_region: str = Field(...)
    aws_access_key_id: str = Field(...)
    aws_secret_access_key: str = Field(...)
    s3_bucket_name: str = Field(...)
    s3_endpoint_url: str = Field(...)

    database_url: str = Field(...)

    log_level: LogLevel = Field(...)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
