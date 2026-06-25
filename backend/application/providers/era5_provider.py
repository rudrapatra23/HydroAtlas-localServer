from __future__ import annotations

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
        try:
            from era5_fetch import ERA5Client
            self._era5_client_class = ERA5Client
        except ImportError:
            raise ProviderError("era5-fetch package not installed")

    async def bootstrap(self, request: BootstrapRequest) -> None:
        from era5_fetch import ERA5Client
        self._client = ERA5Client(request.config)

    async def download(self, request: DownloadRequest) -> DownloadResponse:
        if not self._client:
            raise ProviderError("Provider not bootstrapped")

        era5_request = {
            "variable": request.variable,
            "year": request.year,
            "month": request.month,
            "region": request.region,
        }

        result = await self._client.download(**era5_request)

        return DownloadResponse(
            success=True,
            file_path=result.file_path,
            checksum=result.checksum,
            file_size=result.file_size,
        )

    async def status(self) -> dict:
        return {
            "available": self._client is not None,
            "provider": "era5",
        }

    async def available_periods(self, variable: str) -> Sequence[Tuple[int, int]]:
        raise NotImplementedError("available_periods not yet implemented by era5-fetch")
