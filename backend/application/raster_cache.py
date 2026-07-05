"""Process-level raster acquisition cache.

This module is the single runtime acquisition layer for ERA5-Land
NetCDF tiles read by :class:`~application.raster_computation.RasterComputation`
and :class:`~application.precompute_service.PrecomputeService`.

Guarantees (verified by ``tests/test_raster_cache.py``):

- PostgreSQL ``climate_assets`` is the source of truth for dataset
  existence; this module never contacts CDS.
- A given ``(provider, variable, year, month)`` raster is downloaded at
  most once per concurrent burst; later callers reuse the cached file.
- The local cache file is validated against ``asset.checksum`` (sha256)
  on every read via a cheap head-64KB fingerprint sidecar. Drift
  triggers a transparent re-download from S3.
- Cache writes are atomic (``os.replace`` from a ``.partial`` sibling);
  a half-written file is never exposed as a valid cache entry.
- Total cache size is bounded by ``Settings.raster_cache_max_bytes``
  (default 2 GiB, env-driven); an atime-based LRU sweep evicts oldest
  files first when over budget and never evicts a file held by an
  outstanding :class:`RasterLease`.
- ``max_bytes = 0`` disables the on-disk cache entirely: every acquire
  downloads into a temporary file whose lifetime is bound to the active
  leases.
- The per-key single-flight lock registry is bounded at 4096 entries
  with LRU eviction, so memory cannot grow without bound across
  long-running processes.

Each caller opens its own ``xr.Dataset`` handle from the lease's
``path`` and closes it normally. This module never holds an open
NetCDF file handle beyond the acquisition phase, and never shares
the opened ``Dataset`` object across callers.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from types import TracebackType
from typing import AsyncIterator, Iterable

import xarray as xr

from domain.entities.climate_asset import ClimateAsset
from domain.ports.storage_port import StoragePort
from ingestion.era5.checksums import sha256_file


logger = logging.getLogger("uvicorn.error")


# Single-flight key. Immutable identity per (provider, variable, period).
CacheKey = tuple[str, str, int, int]

# Path-segment allowlist, kept identical to ``ingestion.era5.file_service``
# so the cache layout is the same for both code paths.
_SAFE_SEGMENT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)

_PARTIAL_SUFFIX = ".partial"
_SIDECAR_SUFFIX = ".fp"
_FINGERPRINT_HEAD_BYTES = 65536  # 64 KiB - sub-millisecond sha256 on 10 MB tile
_SWEEP_EVERY_N_ACQUIRES = 16

# Bounds. Per-key lock registry is an LRU; if the working set of unique
# (provider, variable, year, month) keys ever exceeds this many entries,
# the oldest unused lock is dropped.
_MAX_LOCK_REGISTRY_ENTRIES = 4096

# 2 GiB default per user directive - must work correctly regardless of
# total historical dataset size via the LRU sweep. ``0`` disables the
# on-disk cache entirely.
DEFAULT_RASTER_CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024


@dataclass
class RasterLease:
    """Handle returned by :meth:`RasterCache.acquire`.

    The caller MUST eventually call :meth:`release` (or use
    :meth:`RasterCache.leased` as an async context manager) so the
    underlying file is no longer protected from eviction and any
    ephemeral state (the tempfile when ``max_bytes == 0``) is cleaned
    up. Releasing twice is a no-op.
    """

    path: Path
    cache_hit: bool
    bytes_downloaded: int
    source: str
    wait_seconds: float
    download_seconds: float
    validate_seconds: float
    _key: CacheKey
    _is_ephemeral: bool
    _registry: "_LeaseRegistry"
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._registry.release(self._key, self._is_ephemeral, self.path)

    def __enter__(self) -> "RasterLease":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


@dataclass
class OpenRasterHandle:
    """Bundles the opened dataset, cache path, and lease as one ownership unit.

    The three fields must be released together: ``dataset`` is the live
    ``xr.Dataset``/``xr.DataArray`` holding a file handle on ``path``;
    ``lease`` is the :class:`RasterLease` keeping ``path`` out of the
    eviction sweep.

    ``close()`` runs ``dataset.close()`` first and then ``lease.release()``;
    each step is individually try/except'd so a failure in one does not
    skip the other. The handle is also an async context manager. A
    ``__del__`` safety net closes the handle if it is garbage-collected
    without explicit cleanup.
    """

    dataset: xr.Dataset | xr.DataArray
    path: Path
    lease: RasterLease
    _closed: bool = False

    def close(self) -> None:
        """Close the dataset and release the lease. Idempotent.

        Order matters: dataset.close() FIRST (so the netCDF file
        handle is dropped before the lease is released), then
        lease.release() (so the cache file becomes eviction-eligible
        once nothing else can be reading it).

        Emits a structured ``DATASET_CLOSE`` log line so operators can
        correlate an open with its corresponding close and detect
        leaked handles (an open without a matching close is a leak).
        """
        if self._closed:
            return
        self._closed = True
        try:
            self.dataset.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "DATASET_CLOSE key=%s path=%s status=dataset_close_failed error=%s",
                self.lease._key, self.path, exc,
            )
        else:
            logger.info(
                "DATASET_CLOSE key=%s path=%s status=ok",
                self.lease._key, self.path,
            )
        try:
            self.lease.release()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "DATASET_CLOSE key=%s path=%s status=lease_release_failed error=%s",
                self.lease._key, self.path, exc,
            )

    async def __aenter__(self) -> "OpenRasterHandle":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __enter__(self) -> "OpenRasterHandle":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:
        # Last-resort safety net. Prefer explicit ``close()`` or
        # async-context-manager use. We swallow errors here because
        # ``__del__`` runs at unpredictable times (including interpreter
        # shutdown) where logging is unreliable.
        try:
            if not self._closed:
                self.close()
        except Exception:
            pass


# ── Path / key helpers


def _safe_segment(value: str, kind: str) -> str:
    if not value:
        raise ValueError(f"{kind} must be a non-empty string")
    if any(ch not in _SAFE_SEGMENT_CHARS for ch in value):
        raise ValueError(
            f"{kind} {value!r} contains characters outside "
            f"[A-Za-z0-9_-]; refused as a path segment"
        )
    return value


def cache_path_for(cache_root: Path, asset: ClimateAsset) -> Path:
    """Resolve the canonical cache path for ``asset``.

    Layout: ``{cache_root}/{provider}/{variable}/{YYYY}/{MM}.nc``.
    """
    provider_seg = _safe_segment(asset.provider, "provider")
    variable_seg = _safe_segment(asset.variable, "variable")
    relative = Path(
        provider_seg, variable_seg, f"{asset.year:04d}", f"{asset.month:02d}.nc"
    )
    cache_root = cache_root.resolve()
    path = (cache_root / relative).resolve()
    if not path.is_relative_to(cache_root):
        raise ValueError(
            f"cache path {path} escapes cache root {cache_root}"
        )
    return path


def cache_key_for(asset: ClimateAsset) -> CacheKey:
    return (asset.provider, asset.variable, asset.year, asset.month)


# ── Single-flight lock registry


class _LockRegistry:
    """Per-key ``asyncio.Lock`` factory, bounded with LRU eviction.

    Each entry is keyed by ``(cache_key, event_loop_id)`` so a lock is
    never reused across event-loop lifetimes. ``asyncio.run()`` in the
    CLI creates a new loop per invocation; pytest-asyncio's default
    mode creates a fresh loop per test function. Without per-loop
    keys, a cached lock from a closed loop would raise
    ``RuntimeError: ... is bound to a different event loop`` on the
    next call from a new loop.

    The dict is bounded at ``_MAX_LOCK_REGISTRY_ENTRIES`` with LRU
    eviction of the *uncontended* lock at the front. Locks whose loops
    have been closed become unreachable when the loop is GC'd, so the
    bound prevents unbounded growth in long-running processes that
    cycle loops frequently.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Entry value: (loop_id, asyncio.Lock).
        self._data: "OrderedDict[CacheKey, tuple[int, asyncio.Lock]]" = OrderedDict()

    def get_or_create(self, key: CacheKey) -> asyncio.Lock:
        loop_id = _current_loop_id()
        with self._lock:
            entry = self._data.get(key)
            if entry is not None:
                stored_loop_id, lock = entry
                if stored_loop_id == loop_id:
                    # Same loop as the stored lock - reuse it.
                    self._data.move_to_end(key)
                    return lock
                # Loop changed (or the lock belongs to a now-closed
                # loop). Fall through to create a fresh lock. The old
                # one becomes unreachable when its loop is GC'd.
            if len(self._data) >= _MAX_LOCK_REGISTRY_ENTRIES:
                self._data.popitem(last=False)
            new_lock = asyncio.Lock()
            self._data[key] = (loop_id, new_lock)
            return new_lock

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


_lock_registry = _LockRegistry()


def _current_loop_id() -> int:
    """Return the id of the currently running event loop, or 0 if none.

    ``id(loop)`` is stable for the loop's lifetime and is safe to use
    as a dict key. ``0`` is reserved for the no-loop case. Module-level
    (not a method) because it does not use class or instance state.
    """
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return 0


# Per-key open lock registry, separate from the download lock so that
# concurrent cache-hit requests still serialise per file (the download
# lock is only taken on the slow path). The registry is bounded with
# the same LRU discipline as the download lock so memory cannot grow
# without bound across long-running processes that cycle event loops.
_open_lock_registry = _LockRegistry()


class _OpenCounter:
    """Module-level per-key counter of currently in-flight ``open_dataset``
    calls.

    Used by the structured ``OPEN_DATASET`` log line so operators can
    see how many concurrent readers a given raster had when an open
    completed. Also exported as ``active_open_counts()`` for the
    regression test.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[CacheKey, int] = {}

    def enter(self, key: CacheKey) -> int:
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            return self._counts[key]

    def exit(self, key: CacheKey) -> int:
        with self._lock:
            cur = self._counts.get(key, 0)
            if cur <= 1:
                self._counts.pop(key, None)
                return 0
            self._counts[key] = cur - 1
            return cur - 1

    def current(self, key: CacheKey) -> int:
        with self._lock:
            return self._counts.get(key, 0)

    def snapshot(self) -> dict[CacheKey, int]:
        with self._lock:
            return dict(self._counts)


_open_counter = _OpenCounter()


# ── Lease registry


class _LeaseRegistry:
    """Per-key reference counter for outstanding leases.

    The eviction sweep consults :meth:`active_keys` and refuses to
    unlink any cache file whose key has an outstanding lease. When the
    last lease for a key is released, the key is removed from the
    active set; if the lease was ephemeral (``max_bytes == 0``) the
    corresponding tempfile is unlinked.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[CacheKey, int] = {}
        # For max_bytes==0 mode, track the per-key ephemeral path so
        # release() can unlink it when the count hits zero.
        self._ephemeral_paths: dict[CacheKey, Path] = {}

    def acquire(self, key: CacheKey, *, ephemeral: bool, path: Path) -> None:
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            if ephemeral:
                self._ephemeral_paths[key] = path

    def release(self, key: CacheKey, ephemeral: bool, path: Path) -> None:
        with self._lock:
            cnt = self._counts.get(key, 0)
            if cnt <= 1:
                self._counts.pop(key, None)
                if ephemeral:
                    eph = self._ephemeral_paths.pop(key, None)
                    if eph is not None:
                        try:
                            eph.unlink()
                        except OSError:
                            pass
            else:
                self._counts[key] = cnt - 1

    def active_keys(self) -> set[CacheKey]:
        with self._lock:
            return {k for k, v in self._counts.items() if v > 0}


_lease_registry = _LeaseRegistry()


# ── LRU eviction sweep


def _reconstruct_key(root: Path, p: Path) -> CacheKey | None:
    """Inverse of ``cache_path_for``: turn a path back into a key.

    Returns ``None`` for any file that does not match the canonical
    layout — such files are simply skipped by the eviction sweep.
    """
    try:
        rel = p.relative_to(root)
    except ValueError:
        return None
    if len(rel.parts) != 4:
        return None
    provider, variable, year_str, filename = rel.parts
    if not filename.endswith(".nc"):
        return None
    try:
        year = int(year_str[:4])
        month = int(filename[:2])
    except ValueError:
        return None
    return (provider, variable, year, month)


def _cache_size_bytes(
    root: Path, skip_keys: set[CacheKey]
) -> tuple[int, list[tuple[float, int, Path]]]:
    """Return ``(total_bytes, [(atime, size, path), ...])`` for every
    ``*.nc`` under ``root``. Files whose key is in ``skip_keys`` (held
    by an outstanding lease) are excluded. ``.partial`` and ``.fp``
    siblings are always skipped.
    """
    files: list[tuple[float, int, Path]] = []
    total = 0
    if not root.exists():
        return total, files
    for p in root.rglob("*.nc"):
        if p.suffix == _PARTIAL_SUFFIX:
            continue
        key = _reconstruct_key(root, p)
        if key is None:
            continue
        if key in skip_keys:
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        files.append((stat.st_atime, stat.st_size, p))
        total += stat.st_size
    return total, files


def _maybe_sweep_cache(
    cache_root: Path,
    max_bytes: int,
    *,
    active_keys: Iterable[CacheKey] = (),
) -> int:
    """Evict atime-oldest files until ``cache_root`` fits under ``max_bytes``.

    Files held by outstanding leases are protected. Returns the number
    of bytes freed. ``max_bytes <= 0`` is a no-op (the on-disk cache is
    disabled in that mode).
    """
    if max_bytes <= 0 or not cache_root.exists():
        return 0
    skip = set(active_keys)
    total, files = _cache_size_bytes(cache_root, skip_keys=skip)
    if total <= max_bytes:
        return 0
    files.sort(key=lambda item: item[0])  # oldest atime first
    freed = 0
    for _atime, size, path in files:
        if total - freed <= max_bytes:
            break
        try:
            path.unlink()
            freed += size
            # Best-effort: drop the fingerprint sidecar too.
            sidecar = path.with_name(path.name + _SIDECAR_SUFFIX)
            try:
                sidecar.unlink()
            except OSError:
                pass
            logger.info(
                "raster_cache.evict path=%s size=%d reason=size_budget",
                str(path), size,
            )
        except OSError as exc:
            logger.warning(
                "raster_cache.evict_failed path=%s error=%s",
                str(path), exc,
            )
            continue
    return freed


# ── Fingerprint sidecar


def _sidecar_path(cache_path: Path) -> Path:
    return cache_path.with_name(cache_path.name + _SIDECAR_SUFFIX)


def _read_fingerprint_head(path: Path) -> bytes:
    """Read up to ``_FINGERPRINT_HEAD_BYTES`` from the start of ``path``."""
    with path.open("rb") as f:
        return f.read(_FINGERPRINT_HEAD_BYTES)


def _validate_fast(
    cache_path: Path,
    expected_sha: str,
    expected_size: int,
) -> bool:
    """Validate the cache file without reading the whole file.

    Hot-path check: stat size, read sidecar JSON, then sha256 the first
    64 KiB and compare against ``sidecar.sha256_short``. The full
    ``expected_sha`` is only read from the sidecar to confirm the
    artifact is the one we expect — never computed over the file on
    the hot path.

    Returns ``False`` for any failure. The caller falls through to a
    fresh S3 download on ``False``.
    """
    try:
        actual_size = cache_path.stat().st_size
    except OSError:
        return False
    if actual_size != expected_size:
        return False
    sidecar = _sidecar_path(cache_path)
    try:
        sidecar_data = json.loads(sidecar.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if sidecar_data.get("sha256_full") != expected_sha:
        return False
    try:
        head = _read_fingerprint_head(cache_path)
    except OSError:
        return False
    short_actual = hashlib.sha256(head).hexdigest()
    if short_actual != sidecar_data.get("sha256_short"):
        # Sidecar said X, file has Y → drift; treat as invalid.
        logger.warning(
            "raster_cache.fingerprint_drift path=%s expected_short=%s actual_short=%s",
            str(cache_path), sidecar_data.get("sha256_short"), short_actual,
        )
        try:
            cache_path.unlink()
        except OSError:
            pass
        try:
            sidecar.unlink()
        except OSError:
            pass
        return False
    return True


def _publish_with_sidecar(
    partial_path: Path,
    cache_path: Path,
    expected_sha: str,
) -> None:
    """Validate the freshly downloaded ``partial_path`` and atomically
    publish it as ``cache_path`` with a fingerprint sidecar.

    Steps:
      1. ``sha256_file(partial_path)`` — full SHA, required for
         correctness, paid once per cache miss.
      2. Read first 64 KiB and compute the short fingerprint.
      3. Write the sidecar JSON to a ``.fp.partial`` sibling.
      4. ``os.replace(partial_path, cache_path)`` — atomic publish.
      5. ``os.replace(sidecar_partial, sidecar)`` — atomic publish.
    """
    full_actual = sha256_file(partial_path)
    if full_actual != expected_sha:
        raise RuntimeError(
            f"sha256 mismatch for {partial_path}: expected={expected_sha}, actual={full_actual}"
        )
    head = _read_fingerprint_head(partial_path)
    short_actual = hashlib.sha256(head).hexdigest()
    file_size = partial_path.stat().st_size

    sidecar = _sidecar_path(cache_path)
    sidecar_partial = sidecar.with_name(sidecar.name + _PARTIAL_SUFFIX)
    sidecar_partial.write_text(json.dumps({
        "size": file_size,
        "sha256_short": short_actual,
        "sha256_full": full_actual,
        "published_at": time.time(),
    }))
    os.replace(partial_path, cache_path)
    os.replace(sidecar_partial, sidecar)


# ── Cache root resolution


def _resolve_cache_root(cache_root: Path | None) -> Path:
    root = (
        cache_root.resolve()
        if cache_root is not None
        else Path("data/era5/cache").resolve()
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# ── RASTER_ACQUIRE structured log


def _log_acquire(
    *,
    asset: ClimateAsset,
    source: str,
    cache_hit: bool,
    bytes_downloaded: int,
    wait_seconds: float,
    download_seconds: float,
    validate_seconds: float,
) -> None:
    """Emit the structured ``RASTER_ACQUIRE`` summary line."""
    logger.info(
        "RASTER_ACQUIRE key=%s source=%s cache_hit=%s bytes=%d "
        "wait_seconds=%.4f download_seconds=%.4f validate_seconds=%.4f",
        asset.storage_key, source, str(cache_hit).lower(),
        bytes_downloaded, wait_seconds, download_seconds, validate_seconds,
    )


# ── Public RasterCache class


class RasterCache:
    """Process-level raster acquisition cache.

    Constructed cheaply per request inside FastAPI dependency injection
    (one per request is fine); the underlying lock and lease registries
    are module-level singletons so state survives across instances and
    across event-loop lifetimes.
    """

    def __init__(
        self,
        cache_root: Path | None = None,
        max_bytes: int = DEFAULT_RASTER_CACHE_MAX_BYTES,
    ) -> None:
        self._max_bytes = int(max_bytes)
        # Resolve cache root only when on-disk cache is enabled.
        self._cache_root: Path | None = (
            _resolve_cache_root(cache_root) if self._max_bytes > 0 else None
        )
        self._acquire_count = 0
        self._sweep_lock = threading.Lock()

    @property
    def cache_root(self) -> Path | None:
        return self._cache_root

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def on_disk_enabled(self) -> bool:
        return self._max_bytes > 0

    def cache_path_for(self, asset: ClimateAsset) -> Path:
        if self._cache_root is None:
            raise RuntimeError(
                "cache_path_for() is unavailable when max_bytes == 0"
            )
        return cache_path_for(self._cache_root, asset)

    async def acquire(
        self,
        asset: ClimateAsset,
        storage: StoragePort,
    ) -> RasterLease:
        """Return a :class:`RasterLease` for ``asset``.

        Concurrent calls for the same asset coalesce into a single
        download via the per-key :class:`asyncio.Lock`. The on-disk
        cache is validated against ``asset.checksum`` via a cheap
        sidecar fingerprint on the hot path.
        """
        if self._max_bytes > 0:
            return await self._acquire_on_disk(asset, storage)
        return await self._acquire_ephemeral(asset, storage)

    async def leased(
        self,
        asset: ClimateAsset,
        storage: StoragePort,
    ) -> AsyncIterator[RasterLease]:
        """Async context manager wrapping :meth:`acquire` + ``lease.release()``."""
        lease = await self.acquire(asset, storage)
        try:
            yield lease
        finally:
            lease.release()

    async def open_dataset(
        self,
        lease: RasterLease,
        *,
        asset: ClimateAsset | None = None,
    ) -> xr.Dataset | xr.DataArray:
        """Open the NetCDF at ``lease.path`` under the per-key open lock.

        Only one coroutine opens a given ``lease.path`` at a time; the
        lock is keyed on ``cache_key_for(asset)``, not on the whole
        cache, so unrelated rasters proceed in parallel. The lock is
        held only across the ``xr.open_dataset`` call.

        Emits one ``OPEN_DATASET`` log line per call with ``key``,
        ``wait_seconds``, ``open_seconds``, ``status``, and
        ``concurrent_readers``. The caller is responsible for closing
        the dataset and releasing the lease (typically via
        :class:`OpenRasterHandle`).
        """
        key = lease._key
        # Operators grep the OPEN_DATASET log by ``asset.storage_key``
        # (e.g. ``era5-land/surface_runoff/2026/05.nc``), NOT by the
        # full local path which varies across hosts. Fall back to the
        # path when no asset was supplied (e.g. direct unit tests).
        log_key = asset.storage_key if asset is not None else str(lease.path)
        active_before = _open_counter.current(key)
        t_wait_start = time.perf_counter()
        lock = _open_lock_registry.get_or_create(key)
        try:
            await lock.acquire()
        except asyncio.CancelledError:
            # The caller was cancelled while waiting for the open lock.
            # Log so operators can see the cancellation latency and
            # re-raise so the request handler can clean up.
            wait_seconds = time.perf_counter() - t_wait_start
            logger.info(
                "OPEN_DATASET key=%s wait_seconds=%.4f "
                "status=cancelled_before_lock concurrent_readers=%d",
                log_key, wait_seconds, active_before,
            )
            raise
        try:
            wait_seconds = time.perf_counter() - t_wait_start
            active_after_enter = _open_counter.enter(key)
            t_open_start = time.perf_counter()
            try:
                ds = xr.open_dataset(lease.path, engine="netcdf4")
            except asyncio.CancelledError:
                open_seconds = time.perf_counter() - t_open_start
                remaining = _open_counter.exit(key)
                logger.info(
                    "OPEN_DATASET key=%s wait_seconds=%.4f open_seconds=%.4f "
                    "status=cancelled_during_open concurrent_readers=%d",
                    log_key, wait_seconds, open_seconds, remaining,
                )
                raise
            except BaseException:
                open_seconds = time.perf_counter() - t_open_start
                remaining = _open_counter.exit(key)
                logger.warning(
                    "OPEN_DATASET key=%s wait_seconds=%.4f open_seconds=%.4f "
                    "status=open_failed concurrent_readers=%d",
                    log_key, wait_seconds, open_seconds, remaining,
                )
                raise
            open_seconds = time.perf_counter() - t_open_start
            remaining = _open_counter.exit(key)
            logger.info(
                "OPEN_DATASET key=%s wait_seconds=%.4f open_seconds=%.4f "
                "status=ok concurrent_readers=%d",
                log_key, wait_seconds, open_seconds, remaining,
            )
            return ds
        finally:
            lock.release()

    # ── On-disk cache path (max_bytes > 0)

    async def _acquire_on_disk(
        self,
        asset: ClimateAsset,
        storage: StoragePort,
    ) -> RasterLease:
        assert self._cache_root is not None
        cache_path = cache_path_for(self._cache_root, asset)
        key = cache_key_for(asset)
        expected_size = asset.file_size or 0
        active_keys_snapshot = _lease_registry.active_keys()
        self._maybe_sweep(active_keys_snapshot)

        # Fast path: sidecar fingerprint says the file is good.
        t_validate_start = time.perf_counter()
        fast_ok = await asyncio.to_thread(
            _validate_fast, cache_path, asset.checksum, expected_size
        )
        validate_seconds = time.perf_counter() - t_validate_start
        if fast_ok:
            lease = self._register_lease(
                asset, cache_path, cache_hit=True, ephemeral=False,
                source="local-cache", wait_seconds=0.0,
                download_seconds=0.0, validate_seconds=validate_seconds,
            )
            _log_acquire(
                asset=asset, source="local-cache", cache_hit=True,
                bytes_downloaded=0, wait_seconds=0.0, download_seconds=0.0,
                validate_seconds=validate_seconds,
            )
            return lease

        # Slow path: take the per-key lock.
        lock = _lock_registry.get_or_create(key)
        t_wait_start = time.perf_counter()
        async with lock:
            wait_seconds = time.perf_counter() - t_wait_start

            # Re-check inside the lock - a sibling coroutine may have
            # just published the file.
            t_validate_start = time.perf_counter()
            fast_ok = await asyncio.to_thread(
                _validate_fast, cache_path, asset.checksum, expected_size
            )
            validate_seconds = time.perf_counter() - t_validate_start
            if fast_ok:
                lease = self._register_lease(
                    asset, cache_path, cache_hit=True, ephemeral=False,
                    source="local-cache", wait_seconds=wait_seconds,
                    download_seconds=0.0, validate_seconds=validate_seconds,
                )
                _log_acquire(
                    asset=asset, source="local-cache", cache_hit=True,
                    bytes_downloaded=0, wait_seconds=wait_seconds,
                    download_seconds=0.0, validate_seconds=validate_seconds,
                )
                return lease

            # Download to a .partial sibling; atomically publish via the
            # fingerprint-aware publisher (which writes the sidecar too).
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            partial_path = cache_path.with_name(
                cache_path.name + _PARTIAL_SUFFIX
            )
            await asyncio.to_thread(_unlink_if_exists, partial_path)

            t_download_start = time.perf_counter()
            await asyncio.to_thread(
                storage.download, asset.storage_key, partial_path
            )
            download_seconds = time.perf_counter() - t_download_start

            try:
                await asyncio.to_thread(
                    _publish_with_sidecar, partial_path, cache_path, asset.checksum
                )
            except RuntimeError:
                await asyncio.to_thread(_unlink_if_exists, partial_path)
                raise

            lease = self._register_lease(
                asset, cache_path, cache_hit=False, ephemeral=False,
                source="s3", wait_seconds=wait_seconds,
                download_seconds=download_seconds,
                validate_seconds=validate_seconds,
            )
            _log_acquire(
                asset=asset, source="s3", cache_hit=False,
                bytes_downloaded=asset.file_size or cache_path.stat().st_size,
                wait_seconds=wait_seconds, download_seconds=download_seconds,
                validate_seconds=validate_seconds,
            )
            return lease

    # ── Ephemeral path (max_bytes == 0)

    async def _acquire_ephemeral(
        self,
        asset: ClimateAsset,
        storage: StoragePort,
    ) -> RasterLease:
        key = cache_key_for(asset)
        lock = _lock_registry.get_or_create(key)

        async with lock:
            tmp = tempfile.NamedTemporaryFile(suffix=".nc", delete=False)
            tmp.close()
            temp_path = Path(tmp.name)
            try:
                t_download_start = time.perf_counter()
                await asyncio.to_thread(
                    storage.download, asset.storage_key, temp_path
                )
                download_seconds = time.perf_counter() - t_download_start
                t_validate_start = time.perf_counter()
                actual = await asyncio.to_thread(sha256_file, temp_path)
                validate_seconds = time.perf_counter() - t_validate_start
                if actual != asset.checksum:
                    raise RuntimeError(
                        f"S3 download checksum mismatch for {asset.storage_key}: "
                        f"db={asset.checksum}, downloaded={actual}"
                    )
            except Exception:
                await asyncio.to_thread(_unlink_if_exists, temp_path)
                raise

            lease = self._register_lease(
                asset, temp_path, cache_hit=False, ephemeral=True,
                source="s3", wait_seconds=0.0,
                download_seconds=download_seconds,
                validate_seconds=validate_seconds,
            )
            _log_acquire(
                asset=asset, source="s3", cache_hit=False,
                bytes_downloaded=asset.file_size or temp_path.stat().st_size,
                wait_seconds=0.0, download_seconds=download_seconds,
                validate_seconds=validate_seconds,
            )
            return lease

    # ── Internal helpers

    def _register_lease(
        self,
        asset: ClimateAsset,
        path: Path,
        *,
        cache_hit: bool,
        ephemeral: bool,
        source: str,
        wait_seconds: float,
        download_seconds: float,
        validate_seconds: float,
    ) -> RasterLease:
        key = cache_key_for(asset)
        _lease_registry.acquire(key, ephemeral=ephemeral, path=path)
        return RasterLease(
            path=path,
            cache_hit=cache_hit,
            bytes_downloaded=asset.file_size or (path.stat().st_size if path.exists() else 0),
            source=source,
            wait_seconds=wait_seconds,
            download_seconds=download_seconds,
            validate_seconds=validate_seconds,
            _key=key,
            _is_ephemeral=ephemeral,
            _registry=_lease_registry,
            _released=False,
        )

    def _maybe_sweep(self, active_keys: set[CacheKey]) -> None:
        if self._cache_root is None:
            return
        self._acquire_count += 1
        if self._acquire_count % _SWEEP_EVERY_N_ACQUIRES != 0:
            return
        if not self._sweep_lock.acquire(blocking=False):
            return
        try:
            _maybe_sweep_cache(
                self._cache_root, self._max_bytes, active_keys=active_keys,
            )
        finally:
            self._sweep_lock.release()


__all__ = [
    "DEFAULT_RASTER_CACHE_MAX_BYTES",
    "OpenRasterHandle",
    "RasterCache",
    "RasterLease",
    "cache_key_for",
    "cache_path_for",
]
