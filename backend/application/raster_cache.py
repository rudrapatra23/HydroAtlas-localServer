"""A smart cache for our climate data files."""

from __future__ import annotations
from application.native_io_lock import NATIVE_IO_LOCK
from collections import OrderedDict
from dataclasses import dataclass
import asyncio
import hashlib
import itertools
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from types import TracebackType
from typing import AsyncIterator, Iterable, Iterator

import xarray as xr

from core.config import get_settings
from domain.entities.climate_asset import ClimateAsset
from domain.ports.storage_port import StoragePort
from ingestion.era5.checksums import sha256_file
from application.diagnostics import (
    emit_native_event,
    file_cache_maxsize,
    get_request_id,
    runtime_library_versions,
    safe_log,
)


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


# ── Live-handle registry (diagnostic only)
#
# Tracks every OpenRasterHandle that is opened and not yet closed,
# so we can correlate HDF failures with per-key and global handle
# counts. The registry is protected by a module-level lock; all
# mutations happen in finally blocks so diagnostic instrumentation
# cannot alter application behavior.


@dataclass
class RasterLease:
    """A 'lease' on a data file that prevents it from being deleted."""

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
    """A helper that bundles an open data file with its cache lease."""

    dataset: xr.Dataset | xr.DataArray
    path: Path
    lease: RasterLease
    handle_id: str = ""
    _closed: bool = False

    def __post_init__(self) -> None:
        """Register this handle in the global live-handle registry."""
        _register_handle(self)

    async def aclose(self) -> None:
        """Close the dataset and release the lease under the per-key i/o lock."""
        key = self.lease._key
        lock = _dataset_io_lock_registry.get_or_create(key)
        await lock.acquire()
        try:
            if self._closed:
                return
            self._closed = True
            emit_native_event(
                "NATIVE_CLOSE_BEGIN",
                file_path=self.path, cache_key=key,
            )
            try:
                self.dataset.close()
            except Exception as exc:  # noqa: BLE001
                emit_native_event(
                    "NATIVE_CLOSE_ERROR",
                    file_path=self.path, cache_key=key,
                    extra={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )
                safe_log(
                    logging.WARNING,
                    "DATASET_CLOSE key=%s path=%s request_id=%s status=dataset_close_failed error=%s",
                    key, self.path, get_request_id(), exc,
                )
            else:
                emit_native_event(
                    "NATIVE_CLOSE_DONE",
                    file_path=self.path, cache_key=key,
                )
                safe_log(
                    logging.INFO,
                    "DATASET_CLOSE key=%s path=%s request_id=%s status=ok",
                    key, self.path, get_request_id(),
                )
            try:
                self.lease.release()
            except Exception as exc:  # noqa: BLE001
                safe_log(
                    logging.WARNING,
                    "DATASET_CLOSE key=%s path=%s request_id=%s status=lease_release_failed error=%s",
                    key, self.path, get_request_id(), exc,
                )
            finally:
                _unregister_handle(self)
        finally:
            lock.release()

    def close(self) -> None:
        """Best-effort synchronous fallback for non-async call sites."""
        if self._closed:
            return
        self._closed = True
        emit_native_event(
            "NATIVE_CLOSE_BEGIN",
            file_path=self.path, cache_key=self.lease._key,
        )
        try:
            self.dataset.close()
        except Exception as exc:  # noqa: BLE001
            emit_native_event(
                "NATIVE_CLOSE_ERROR",
                file_path=self.path, cache_key=self.lease._key,
                extra={
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            )
            safe_log(
                logging.WARNING,
                "DATASET_CLOSE key=%s path=%s request_id=%s status=dataset_close_failed_sync error=%s",
                self.lease._key, self.path, get_request_id(), exc,
            )
        else:
            emit_native_event(
                "NATIVE_CLOSE_DONE",
                file_path=self.path, cache_key=self.lease._key,
            )
            safe_log(
                logging.INFO,
                "DATASET_CLOSE key=%s path=%s request_id=%s status=ok_sync",
                self.lease._key, self.path, get_request_id(),
            )
        try:
            self.lease.release()
        except Exception as exc:  # noqa: BLE001
            safe_log(
                logging.WARNING,
                "DATASET_CLOSE key=%s path=%s request_id=%s status=lease_release_failed_sync error=%s",
                self.lease._key, self.path, get_request_id(), exc,
            )
        finally:
            _unregister_handle(self)

    async def __aenter__(self) -> "OpenRasterHandle":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

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
    """Resolve the canonical cache path for ``asset``."""
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


# ── Live-handle registry (diagnostic only)
#
# Tracks every OpenRasterHandle that is opened and not yet closed,
# so we can correlate HDF failures with per-key and global handle
# counts. The registry is protected by a module-level lock; all
# mutations happen in finally blocks so diagnostic instrumentation
# cannot alter application behavior.

_handle_id_counter: Iterator[int] = itertools.count(start=1)
_live_handle_lock: threading.Lock = threading.Lock()
_live_handles_by_key: dict[CacheKey, set[str]] = {}
_global_live_handle_count: int = 0


def _register_handle(handle: OpenRasterHandle) -> None:
    """Register a newly-created ``openrasterhandle`` in the live-handle."""
    global _global_live_handle_count
    with _live_handle_lock:
        try:
            handle_id = f"{next(_handle_id_counter)}"
            handle.handle_id = handle_id
            key = handle.lease._key
            _live_handles_by_key.setdefault(key, set()).add(handle_id)
            _global_live_handle_count += 1
        finally:
            # no-op; lock released implicitly
            pass
    per_key = len(_live_handles_by_key.get(handle.lease._key, {}))
    safe_log(
        logging.INFO,
        "OPEN_HANDLE request_id=%s key=%s path=%s handle_id=%s "
        "per_key_live=%d global_live=%d",
        get_request_id(), handle.lease._key, handle.path,
        handle_id, per_key, _global_live_handle_count,
    )


def _unregister_handle(handle: OpenRasterHandle) -> None:
    """Remove a closed ``openrasterhandle`` from the live-handle registry."""
    global _global_live_handle_count
    with _live_handle_lock:
        try:
            key = handle.lease._key
            handles_for_key = _live_handles_by_key.get(key)
            if handles_for_key is not None:
                handles_for_key.discard(handle.handle_id)
                if not handles_for_key:
                    _live_handles_by_key.pop(key, None)
            _global_live_handle_count -= 1
        finally:
            pass
    safe_log(
        logging.INFO,
        "CLOSE_HANDLE request_id=%s key=%s path=%s handle_id=%s "
        "per_key_live_after=%d global_live_after=%d",
        get_request_id(), handle.lease._key, handle.path,
        handle.handle_id,
        len(_live_handles_by_key.get(handle.lease._key, {})),
        _global_live_handle_count,
    )


# ── Single-flight lock registry


class _LockRegistry:
    """Per-key ``asyncio."""

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
    """Return the id of the currently running event loop, or 0 if none."""
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return 0


# Per-key dataset I/O lock registry, separate from the download lock so that
# concurrent cache-hit requests still serialise open/close per file (the
# download lock is only taken on the slow path). The registry is bounded with
# the same LRU discipline as the download lock so memory cannot grow without
# bound across long-running processes that cycle event loops.
_dataset_io_lock_registry = _LockRegistry()


class _OpenCounter:
    """Module-level per-key counter of currently in-flight ``open_dataset``."""

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
    """Per-key reference counter for outstanding leases."""

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
    """Inverse of ``cache_path_for``: turn a path back into a key."""
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
    """Return ``(total_bytes, [(atime, size, path), ."""
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
    """Evict atime-oldest files until ``cache_root`` fits under ``max_bytes``."""
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
    """Read up to ``_fingerprint_head_bytes`` from the start of ``path``."""
    with path.open("rb") as f:
        return f.read(_FINGERPRINT_HEAD_BYTES)


def _validate_fast(
    cache_path: Path,
    expected_sha: str,
    expected_size: int,
) -> bool:
    """Validate the cache file without reading the whole file."""
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
    """Validate the freshly downloaded ``partial_path`` and atomically."""
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
        else get_settings().raster_cache_root_resolved()
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
    """Emit the structured ``raster_acquire`` summary line."""
    logger.info(
        "RASTER_ACQUIRE key=%s source=%s cache_hit=%s bytes=%d "
        "wait_seconds=%.4f download_seconds=%.4f validate_seconds=%.4f",
        asset.storage_key, source, str(cache_hit).lower(),
        bytes_downloaded, wait_seconds, download_seconds, validate_seconds,
    )


# ── Public RasterCache class


class RasterCache:
    """The main cache manager."""

    def __init__(
        self,
        cache_root: Path | None = None,
        max_bytes: int | None = None,
    ) -> None:
        settings = None
        if cache_root is None or max_bytes is None:
            settings = get_settings()
        if max_bytes is None:
            assert settings is not None
            max_bytes = int(settings.raster_cache_max_bytes)
        self._max_bytes = int(max_bytes)
        # Resolve cache root only when on-disk cache is enabled.
        self._cache_root: Path | None = (
            _resolve_cache_root(
                cache_root
                if cache_root is not None
                else settings.raster_cache_root_resolved() if settings is not None else None
            )
            if self._max_bytes > 0 else None
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
        """Return a :class:`rasterlease` for ``asset``."""
        if self._max_bytes > 0:
            return await self._acquire_on_disk(asset, storage)
        return await self._acquire_ephemeral(asset, storage)

    async def leased(
        self,
        asset: ClimateAsset,
        storage: StoragePort,
    ) -> AsyncIterator[RasterLease]:
        """Async context manager wrapping :meth:`acquire` + ``lease."""
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
        """Opens a data file for reading, making sure only one task does it at a time."""
        key = lease._key
        # Operators grep the OPEN_DATASET log by ``asset.storage_key``
        # (e.g. ``era5-land/surface_runoff/2026/05.nc``), NOT by the
        # full local path which varies across hosts. Fall back to the
        # path when no asset was supplied (e.g. direct unit tests).
        log_key = asset.storage_key if asset is not None else str(lease.path)
        active_before = _open_counter.current(key)
        t_wait_start = time.perf_counter()
        lock = _dataset_io_lock_registry.get_or_create(key)
        try:
            await lock.acquire()
        except asyncio.CancelledError:
            # The caller was cancelled while waiting for the open lock.
            # Log so operators can see the cancellation latency and
            # re-raise so the request handler can clean up.
            wait_seconds = time.perf_counter() - t_wait_start
            logger.info(
                "OPEN_DATASET key=%s wait_seconds=%.4f "
                "status=cancelled_before_lock concurrent_readers=%d request_id=%s",
                log_key, wait_seconds, active_before, get_request_id(),
            )
            raise
        try:
            wait_seconds = time.perf_counter() - t_wait_start
            active_after_enter = _open_counter.enter(key)
            t_open_start = time.perf_counter()
            try:
                emit_native_event(
                    "NATIVE_OPEN_BEGIN",
                    file_path=lease.path, cache_key=key,
                    extra={"wait_seconds": wait_seconds},
                )
                with NATIVE_IO_LOCK:
                    ds = xr.open_dataset(lease.path, engine="netcdf4")
                    ds.load()
                emit_native_event(
                    "NATIVE_OPEN_DONE",
                    file_path=lease.path, cache_key=key,
                    extra={"open_seconds": time.perf_counter() - t_open_start},
                )
            except asyncio.CancelledError as exc:
                emit_native_event(
                    "NATIVE_OPEN_ERROR",
                    file_path=lease.path, cache_key=key,
                    extra={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc) or "cancelled_during_open",
                        "open_seconds": time.perf_counter() - t_open_start,
                    },
                )
                open_seconds = time.perf_counter() - t_open_start
                remaining = _open_counter.exit(key)
                logger.info(
                    "OPEN_DATASET key=%s wait_seconds=%.4f open_seconds=%.4f "
                    "status=cancelled_during_open concurrent_readers=%d request_id=%s",
                    log_key, wait_seconds, open_seconds, remaining, get_request_id(),
                )
                raise
            except BaseException as exc:
                emit_native_event(
                    "NATIVE_OPEN_ERROR",
                    file_path=lease.path, cache_key=key,
                    extra={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                        "open_seconds": time.perf_counter() - t_open_start,
                    },
                )
                open_seconds = time.perf_counter() - t_open_start
                remaining = _open_counter.exit(key)
                with _live_handle_lock:
                    per_key_live_handles = len(_live_handles_by_key.get(key, set()))
                    global_live_handles = _global_live_handle_count
                same_key_live_handle_exists = bool(_live_handles_by_key.get(key))
                safe_log(
                    logging.WARNING,
                    "OPEN_DATASET key=%s wait_seconds=%.4f open_seconds=%.4f "
                    "status=open_failed concurrent_readers=%d request_id=%s "
                    "per_key_live_handles=%d global_live_handles=%d "
                    "file_cache_maxsize=%d runtime_library_versions=%s "
                    "same_key_live_handle_exists=%s",
                    log_key, wait_seconds, open_seconds, remaining, get_request_id(),
                    per_key_live_handles, global_live_handles,
                    file_cache_maxsize(), runtime_library_versions(),
                    same_key_live_handle_exists,
                )
                raise
            open_seconds = time.perf_counter() - t_open_start
            remaining = _open_counter.exit(key)
            with _live_handle_lock:
                per_key_live_handles = len(_live_handles_by_key.get(key, set()))
                global_live_handles = _global_live_handle_count
            safe_log(
                logging.INFO,
                "OPEN_DATASET key=%s wait_seconds=%.4f open_seconds=%.4f "
                "status=ok concurrent_readers=%d request_id=%s "
                "per_key_live_handles=%d global_live_handles=%d "
                "file_cache_maxsize=%d runtime_library_versions=%s",
                log_key, wait_seconds, open_seconds, remaining, get_request_id(),
                per_key_live_handles, global_live_handles,
                file_cache_maxsize(), runtime_library_versions(),
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
