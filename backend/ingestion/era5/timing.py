"""PhaseTimer + structured summary logger for the era5 ingestion pipeline.

Each ``Downloader.ensure_dataset`` call exercises up to four distinct
subsystems (metadata, S3, CDS, S3 upload). A single structured log line
per call lets operators answer "why did this take 8 seconds?" without
grepping through stdout.

This module is the single source of truth for that log format. The
contract:

- :class:`PhaseTimer` is a context manager used by ``Downloader.ensure_dataset``
  to record the elapsed wall time of each named phase (e.g. ``metadata_lookup``,
  ``s3_download``). It does not log on its own — that is the caller's job —
  so the timer stays trivially testable.
- :func:`log_ensure_summary` emits the canonical structured summary line at
  ``logger.info``. Callers pass the phases they actually ran and the
  booleans that drive the log shape (``cache_hit``, ``source``).
- :class:`EnsureSummary` is a frozen dataclass that mirrors the JSON-shaped
  payload. It exists for callers that want to serialize the summary to
  somewhere other than the logger (tests, /status JSON, etc.).

The log keys match the table in ``.kimchi/docs/era5-pg-source-of-truth.md``:
``event``, ``provider``, ``variable``, ``year``, ``month``,
``metadata_lookup_ms``, ``s3_download_ms``, ``era5_download_ms``,
``s3_upload_ms``, ``local_cache_hit``, ``source``, ``total_ms``.

Example::

    timer = PhaseTimer()
    with timer.phase("metadata_lookup"):
        asset = await repository.get_by_period(...)
    log_ensure_summary(
        logger,
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=5,
        phases=timer.elapsed_ms,
        cache_hit=True,
        source="db",
        total_ms=timer.total_ms(),
    )
"""

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
    """Records elapsed wall time for each named phase.

    Usage::

        timer = PhaseTimer()
        with timer.phase("metadata_lookup"):
            await something()
        with timer.phase("s3_download"):
            ...
        # timer.elapsed_ms == {"metadata_lookup": 12.3, "s3_download": 240.1}
        # timer.total_ms() == 252.4
    """

    _starts: dict[str, float] = field(default_factory=dict)
    elapsed_ms: dict[str, float] = field(default_factory=dict)
    _total_started_at: float | None = None

    def start_total(self) -> None:
        """Reset the global timer. Idempotent — call once at the top of
        ``ensure_dataset`` to anchor the ``total_ms`` measurement.
        """
        self._total_started_at = time.perf_counter()

    def total_ms(self) -> float:
        if self._total_started_at is None:
            return 0.0
        return (time.perf_counter() - self._total_started_at) * 1000.0

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Record the wall time of the enclosed block under ``name``.

        Re-entrant for the same name: the second ``phase("x")`` call
        accumulates onto the first. Phases that never run simply do not
        appear in ``elapsed_ms``; the summary log treats them as 0.
        """
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
    """The canonical structured payload for an ``ensure_dataset`` call.

    Field names match the documented log keys exactly so ``asdict(...)``
    round-trips into the JSON-shaped ``extra=`` payload that
    :func:`log_ensure_summary` emits.
    """

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
    """Build an :class:`EnsureSummary` from a phase map and the booleans
    that drive its shape. Missing phases are reported as 0 ms.
    """
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
    """Emit the canonical structured summary line.

    The payload is JSON-encoded into ``extra=`` so log aggregators can index
    the fields directly. A short ``message`` is also emitted so operators
    tail-ing the log still see what happened without a JSON parser.
    """
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
    """Stable JSON encoding used by tests that assert the log payload.

    The :class:`logging.LogRecord` ``extra=`` mapping is internally
    type-coerced; tests that capture the record want a deterministic
    string instead.
    """
    return json.dumps(summary.to_dict(), sort_keys=True, default=str)
