from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence, Tuple

from application.providers.provider import Provider
from application.dto.requests import BootstrapRequest, DownloadRequest
from application.dto.responses import DownloadResponse


class ProviderError(Exception):
    """Error raised when a provider encounters a problem."""


class ERA5Provider(Provider):
    """Provider for ERA5 climate data. Pure adapter for era5-fetch package."""

    def __init__(self):
        self._client = None
        self._config = None
        try:
            from era5_fetch import ERA5Client
            self._era5_client_class = ERA5Client
        except ImportError:
            raise ProviderError("era5-fetch package not installed")

    async def bootstrap(self, request: BootstrapRequest) -> None:
        from era5_fetch.core.config import Config
        from era5_fetch.services.manifest_manager import ManifestManager
        from era5_fetch.services.file_service import FileService

        config_dict = request.config or {}
        
        self._config = Config(
            storage_root=Path(config_dict.get("storage_root", "/tmp/era5_fetch")),
            storage_dir=Path(config_dict.get("storage_dir", "/tmp/era5_fetch/data")),
            logs_dir=Path(config_dict.get("logs_dir", "/tmp/era5_fetch/logs")),
            manifest_path=Path(config_dict.get("manifest_path", "/tmp/era5_fetch/manifest.json")),
            temp_dir=Path(config_dict.get("temp_dir", "/tmp/era5_fetch/temp")),
            locks_dir=Path(config_dict.get("locks_dir", "/tmp/era5_fetch/locks")),
            dataset=config_dict.get("dataset", "reanalysis-era5-land-monthly-means"),
            variables=tuple(config_dict.get("variables", [])) if config_dict.get("variables") else None,
            cds_api_key=config_dict.get("cds_api_key"),
            cds_api_url=config_dict.get("cds_api_url"),
        )
        
        manifest = ManifestManager(self._config)
        files = FileService(self._config)
        logger = logging.getLogger("era5_fetch")
        
        self._client = self._era5_client_class(
            config=self._config,
            manifest=manifest,
            files=files,
            logger=logger,
        )

    async def download(self, request: DownloadRequest) -> DownloadResponse:
        if not self._client:
            raise ProviderError("Provider not bootstrapped")

        result = self._client.ensure_downloaded(request.year, request.month)

        if result.success:
            return DownloadResponse(
                success=True,
                file_path=result.local_path,
                checksum=result.checksum,
                file_size=result.file_size,
            )
        else:
            return DownloadResponse(
                success=False,
                error_message=f"Download failed for {request.year}-{request.month:02d}",
            )

    async def status(self) -> dict:
        return {
            "available": self._client is not None,
            "provider": "era5",
        }

    async def available_periods(self, variable: str) -> Sequence[Tuple[int, int]]:
        raise NotImplementedError("available_periods not yet implemented by era5-fetch")
