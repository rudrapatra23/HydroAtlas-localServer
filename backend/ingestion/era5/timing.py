"""Phasetimer + structured summary logger for the era5 ingestion pipeline."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import json
import logging
import time
from typing import Iterator, Literal


# Phases recognised by the structured summary. New phases must be added here
# AND to the summary dataclass so the log shape stays a closed set.
PHASE_METADATA_LOOKUP = "metadata_lookup"
PHASE_S3_DOWNLOAD = "s3_download"
PHASE_ERA5_DOWNLOAD = "era5_download"
PHASE_S3_UPLOAD = "s3_upload"

EVENT_ENSURE = "dataset.ensure"


Source = Literal["db", "era5"]
CacheHit = bool | Literal["n/a"]


@dataclass
class PhaseTimer:
    """Records elapsed wall time for each named phase."""

    _starts: dict[str, float] = field(default_factory=dict)
    elapsed_ms: dict[str, float] = field(default_factory=dict)
    _total_started_at: float | None = None

    def start_total(self) -> None:
        """Reset the global timer."""
        self._total_started_at = time.perf_counter()

    def total_ms(self) -> float:
        if self._total_started_at is None:
            return 0.0
        return (time.perf_counter() - self._total_started_at) * 1000.0

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Record the wall time of the enclosed block under ``name``."""
        start = time.perf_counter()
        self._starts[name] = start
        try:
            yield
        finally:
            prior = self.elapsed_ms.get(name, 0.0)
            self.elapsed_ms[name] = prior + (time.perf_counter() - start) * 1000.0
            self._starts.pop(name, None)


@dataclass(frozen=True)
class EnsureSummary:
    """The canonical structured payload for an ``ensure_dataset`` call."""

    event: str
    provider: str
    variable: str
    year: int
    month: int
    metadata_lookup_ms: float
    s3_download_ms: float
    era5_download_ms: float
    s3_upload_ms: float
    local_cache_hit: CacheHit
    source: Source
    total_ms: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_ensure_summary(
    *,
    provider: str,
    variable: str,
    year: int,
    month: int,
    phases: dict[str, float],
    cache_hit: CacheHit,
    source: Source,
    total_ms: float,
) -> EnsureSummary:
    """Build an :class:`ensuresummary` from a phase map and the booleans."""
    return EnsureSummary(
        event=EVENT_ENSURE,
        provider=provider,
        variable=variable,
        year=year,
        month=month,
        metadata_lookup_ms=float(phases.get(PHASE_METADATA_LOOKUP, 0.0)),
        s3_download_ms=float(phases.get(PHASE_S3_DOWNLOAD, 0.0)),
        era5_download_ms=float(phases.get(PHASE_ERA5_DOWNLOAD, 0.0)),
        s3_upload_ms=float(phases.get(PHASE_S3_UPLOAD, 0.0)),
        local_cache_hit=cache_hit,
        source=source,
        total_ms=float(total_ms),
    )


def log_ensure_summary(
    logger: logging.Logger,
    *,
    provider: str,
    variable: str,
    year: int,
    month: int,
    phases: dict[str, float],
    cache_hit: CacheHit,
    source: Source,
    total_ms: float,
    level: int = logging.INFO,
) -> EnsureSummary:
    """Emit the canonical structured summary line."""
    summary = build_ensure_summary(
        provider=provider,
        variable=variable,
        year=year,
        month=month,
        phases=phases,
        cache_hit=cache_hit,
        source=source,
        total_ms=total_ms,
    )
    payload = summary.to_dict()
    message = (
        f"dataset.ensure provider={provider} variable={variable} "
        f"period={year:04d}-{month:02d} "
        f"cache_hit={payload['local_cache_hit']!s} source={source} "
        f"total_ms={total_ms:.1f}"
    )
    logger.log(level, message, extra=payload)
    return summary


def encode_summary_for_extra(summary: EnsureSummary) -> str:
    """Stable json encoding used by tests that assert the log payload."""
    return json.dumps(summary.to_dict(), sort_keys=True, default=str)
