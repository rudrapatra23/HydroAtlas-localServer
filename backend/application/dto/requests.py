from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class BootstrapRequest:
    config: Optional[dict] = None


@dataclass
class DownloadRequest:
    provider: str
    variable: str
    year: int
    month: int
    region: Optional[tuple[float, float, float, float]] = None
