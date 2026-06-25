from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence, Tuple

from application.dto.requests import BootstrapRequest, DownloadRequest
from application.dto.responses import DownloadResponse


class Provider(ABC):
    @abstractmethod
    async def bootstrap(self, request: BootstrapRequest) -> None:
        raise NotImplementedError

    @abstractmethod
    async def download(self, request: DownloadRequest) -> DownloadResponse:
        raise NotImplementedError

    @abstractmethod
    async def status(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def available_periods(self, variable: str) -> Sequence[Tuple[int, int]]:
        raise NotImplementedError
