"""System health and diagnostic tools: crash tracking, version logging, and request tracing."""
from __future__ import annotations

import asyncio
import contextvars
import faulthandler
import gc
import logging
import os
import platform
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


logger = logging.getLogger("uvicorn.error")

# Where we store crash logs. Used by our troubleshooting scripts.
_crash_log_path: Path | None = None
_setup_done: bool = False

# Keeps track of the unique ID for each request as it moves through the system.
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hydroatlas_request_id", default=None
)


def setup_diagnostics(crash_log_path: Path | None = None) -> Path:
    """Sets up crash tracking and logs some basic startup info."""
    global _crash_log_path, _setup_done

    if _setup_done:
        if crash_log_path is not None and _crash_log_path != crash_log_path:
            logger.warning(
                "DIAGNOSTICS_RECONFIGURE requested_path=%s active_path=%s; ignoring",
                crash_log_path, _crash_log_path,
            )
        return _crash_log_path  # type: ignore[return-value]

    if crash_log_path is None:
        crash_log_path = Path("crash.log")
    crash_log_path = crash_log_path.resolve()

    # Line-buffered, unbuffered on flush. Opened before faulthandler so
    # a crash during library import still has a valid file descriptor.
    try:
        crash_file = open(crash_log_path, "w", buffering=1)
    except OSError as exc:
        logger.warning(
            "DIAGNOSTICS_CRASH_LOG_OPEN_FAILED path=%s error=%s; faulthandler to stderr",
            crash_log_path, exc,
        )
        # Fall back to stderr — still captures native crashes.
        faulthandler.enable(all_threads=True)
        _crash_log_path = crash_log_path
        _setup_done = True
        _log_startup_diagnostics()
        return crash_log_path

    try:
        faulthandler.enable(crash_file, all_threads=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "DIAGNOSTICS_FAULTHANDLER_ENABLE_FAILED error=%s; continuing without faulthandler",
            exc,
        )
        try:
            crash_file.close()
        except OSError:
            pass

    _crash_log_path = crash_log_path
    _setup_done = True

    # Write a header to crash.log so the file is not empty even if no
    # crash occurs. Operators can grep for the startup line.
    try:
        crash_file.write(
            f"# HydroAtlas crash.log\n"
            f"# pid={os.getpid()} python={platform.python_version()} "
            f"platform={platform.platform()}\n"
            f"# faulthandler: all_threads=True\n"
            f"#\n"
        )
        crash_file.flush()
    except Exception:  # noqa: BLE001
        pass

    _log_startup_diagnostics()
    return crash_log_path


def _log_startup_diagnostics() -> None:
    """Emit the structured startup diagnostics lines."""
    pid = os.getpid()
    py_version = platform.python_version()
    platform_str = platform.platform()
    thread_count = threading.active_count()

    logger.info(
        "DIAGNOSTICS_STARTUP pid=%d python_version=%s platform=%s thread_count=%d "
        "crash_log=%s",
        pid, py_version, platform_str, thread_count, _crash_log_path,
    )

    _log_library_versions()
    _log_process_resources()


def _log_library_versions() -> None:
    versions: dict[str, str] = {}
    for mod_name in ("xarray", "netCDF4", "numpy", "rasterio", "rioxarray"):
        try:
            mod = __import__(mod_name)
            versions[mod_name] = getattr(mod, "__version__", "unknown")
        except ImportError:
            versions[mod_name] = "NOT INSTALLED"
        except Exception as exc:  # noqa: BLE001
            versions[mod_name] = f"IMPORT_ERROR: {exc}"
    logger.info("DIAGNOSTICS_VERSIONS %s", versions)

    # HDF5 / netCDF C library versions via the netCDF4 Python binding.
    for attr in ("__netcdf4libversion__", "__hdf5libversion__"):
        try:
            import netCDF4  # type: ignore[import-not-found]
            ver = getattr(netCDF4, attr, None)
            if ver:
                logger.info("DIAGNOSTICS_C_LIB %s=%s", attr, ver)
        except Exception:  # noqa: BLE001
            pass

    # rioxarray often wraps rasterio; record rasterio.__gdal_version__
    # if available.
    try:
        import rasterio  # type: ignore[import-not-found]
        gdal_ver = getattr(rasterio, "__gdal_version__", None)
        if gdal_ver:
            logger.info("DIAGNOSTICS_C_LIB rasterio.__gdal_version__=%s", gdal_ver)
    except Exception:  # noqa: BLE001
        pass


def _log_process_resources() -> None:
    """Logs how much memory the server is using and how many files it has open."""
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        logger.info("DIAGNOSTICS_RESOURCES psutil=NOT_INSTALLED (skipping handle count)")
        return

    try:
        process = psutil.Process(os.getpid())
        mem = process.memory_info()
        num_handles = -1
        if hasattr(process, "num_handles"):
            try:
                num_handles = process.num_handles()
            except Exception as exc:  # noqa: BLE001
                logger.info("DIAGNOSTICS_RESOURCES num_handles_failed error=%s", exc)
        logger.info(
            "DIAGNOSTICS_RESOURCES pid=%d rss_bytes=%d num_handles=%s "
            "thread_count=%d",
            os.getpid(), mem.rss, num_handles, threading.active_count(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("DIAGNOSTICS_RESOURCES snapshot_failed error=%s", exc)


@contextmanager
def request_context(request_id: str | None = None) -> Iterator[str]:
    """Sets a unique id for a request that stays with it as it's being processed."""
    if request_id is None:
        request_id = uuid.uuid4().hex[:12]
    token = _request_id_var.set(request_id)
    try:
        yield request_id
    finally:
        _request_id_var.reset(token)


def get_request_id() -> str:
    """Return the current request_id, or ``"-"`` if no request context is set."""
    return _request_id_var.get() or "-"


def get_crash_log_path() -> Path | None:
    """Return the path of the active crash."""
    return _crash_log_path


def flush() -> None:
    """Flush all logging handlers, stderr, and stdout."""
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:  # noqa: BLE001
            pass
    for stream in (sys.stderr, sys.stdout):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass


def safe_log(level: int, msg: str, *args: object) -> None:
    """A 'safer' way to log diagnostic info that won't break the app."""
    try:
        logger.log(level, msg, *args)
    except Exception:  # noqa: BLE001
        # Diagnostic-only failure; do not propagate. See docstring.
        pass


def runtime_library_versions() -> dict[str, str]:
    """Return installed versions of every library involved in the."""
    versions: dict[str, str] = {}
    versions["python"] = platform.python_version()
    try:
        import xarray
        versions["xarray"] = xarray.__version__
    except Exception:
        versions["xarray"] = "-"
    try:
        import netCDF4
        versions["netcdf4"] = netCDF4.__version__
        versions["netcdf4_c"] = getattr(netCDF4, "__netcdf4libversion__", "-")
        versions["hdf5"] = getattr(netCDF4, "__hdf5libversion__", "-")
    except Exception:
        versions["netcdf4"] = "-"
        versions["netcdf4_c"] = "-"
        versions["hdf5"] = "-"
    try:
        import rasterio
        versions["rasterio"] = rasterio.__version__
        versions["rasterio_gdal"] = getattr(rasterio, "__gdal_version__", "-")
    except Exception:
        versions["rasterio"] = "-"
        versions["rasterio_gdal"] = "-"
    try:
        import rioxarray
        versions["rioxarray"] = rioxarray.__version__
    except Exception:
        versions["rioxarray"] = "-"
    return versions


def file_cache_maxsize() -> int:
    try:
        import xarray.core.options as opts
        return opts.OPTIONS["file_cache_maxsize"]
    except Exception:
        return 128


# ── Native lifecycle snapshot (DIAGNOSTIC ONLY)
#
# A single best-effort diagnostic snapshot helper that captures
# process/resource/cache state at native open/close boundaries.
#
# Constraints:
#   - MUST NEVER alter request behavior.
#   - Every metric collection is wrapped in try/except so a metric
#     failure cannot fail the request.
#   - MUST NOT install dependencies. If psutil is unavailable, fall
#     back to stdlib + ctypes (Windows only).
#   - MUST NOT trigger gc.collect().
#   - MUST NOT retain references to Dataset / FileManager objects.
#   - MUST NOT change xarray cache size.

# Persistent sink path + lock. The lock is for file I/O serialization
# only — it is not held around any native open/close call.
_lifecycle_log_path: Path | None = None
_lifecycle_log_lock: threading.Lock = threading.Lock()


def setup_lifecycle_log(path: Path | None = None) -> Path:
    """Configure the persistent native_* event sink."""
    global _lifecycle_log_path
    if path is None:
        path = (Path(__file__).resolve().parent.parent / "native_lifecycle.log")
    else:
        path = path.resolve()
    with _lifecycle_log_lock:
        if _lifecycle_log_path is None:
            _lifecycle_log_path = path
            try:
                with path.open("a", encoding="utf-8"):
                    pass
                logger.info(
                    "DIAGNOSTICS_LIFECYCLE_LOG path=%s", str(path),
                )
            except OSError as exc:
                logger.warning(
                    "DIAGNOSTICS_LIFECYCLE_LOG_OPEN_FAILED path=%s error=%s",
                    str(path), exc,
                )
        else:
            if _lifecycle_log_path != path:
                logger.warning(
                    "DIAGNOSTICS_LIFECYCLE_LOG_RECONFIGURE requested=%s active=%s; ignoring",
                    str(path), str(_lifecycle_log_path),
                )
    return _lifecycle_log_path


def get_lifecycle_log_path() -> Path | None:
    """Return the configured lifecycle-log path, or ``none``."""
    return _lifecycle_log_path


def _safe_call(fn: object, *args: object) -> object:
    """Invoke ``fn(*args)`` and return its result, or ``none`` on error."""
    try:
        return fn(*args)  # type: ignore[call-arg]
    except Exception:  # noqa: BLE001
        return None


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[call-arg]
    except Exception:  # noqa: BLE001
        return None


def _read_file_cache_occupancy() -> int | None:
    """Return ``len(xarray."""
    try:
        from xarray.backends.file_manager import FILE_CACHE
        return len(FILE_CACHE)
    except Exception:  # noqa: BLE001
        return None


def _read_rss_bytes() -> int | None:
    """Return process rss in bytes."""
    try:
        import psutil  # type: ignore[import-not-found]
        return int(psutil.Process(os.getpid()).memory_info().rss)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return None
    # POSIX fallback only. Windows has no portable stdlib RSS accessor.
    if sys.platform == "win32":
        return None
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # Linux returns KiB; macOS returns bytes. Heuristic: if it
        # looks like KiB (value < 1e9 for a single process), convert.
        rss = int(usage.ru_maxrss)
        if sys.platform == "darwin":
            return rss
        return rss * 1024
    except Exception:  # noqa: BLE001
        return None


def _read_windows_handle_count() -> int | None:
    """Return the windows process handle count."""
    try:
        import psutil  # type: ignore[import-not-found]
        return int(psutil.Process(os.getpid()).num_handles())
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return None
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        handle_count = wintypes.DWORD(0)
        ok = kernel32.GetProcessHandleCount(
            kernel32.GetCurrentProcess(),
            ctypes.byref(handle_count),
        )
        if ok:
            return int(handle_count.value)
    except Exception:  # noqa: BLE001
        pass
    return None


def _read_gc_counts() -> tuple[int, int, int] | None:
    """Return ``gc."""
    try:
        counts = gc.get_count()
        return (int(counts[0]), int(counts[1]), int(counts[2]))
    except Exception:  # noqa: BLE001
        return None


def _read_current_task_id() -> int | None:
    """Return ``id(asyncio."""
    try:
        return id(asyncio.current_task())
    except Exception:  # noqa: BLE001
        return None


def _read_live_handle_counts(cache_key: object | None) -> tuple[int | None, int | None]:
    """Return ``(global_live, same_key_live)`` from ``raster_cache``."""
    try:
        from application.raster_cache import (
            _global_live_handle_count,
            _live_handle_lock,
            _live_handles_by_key,
        )
        with _live_handle_lock:
            global_count = int(_global_live_handle_count)
            if cache_key is None:
                same_key = 0
            else:
                same_key = len(_live_handles_by_key.get(cache_key, set()))  # type: ignore[arg-type]
            return global_count, int(same_key)
    except Exception:  # noqa: BLE001
        return None, None


def _capture_lifecycle_snapshot(
    *,
    event: str,
    file_path: object | None = None,
    cache_key: object | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the structured native_* snapshot dict."""
    try:
        global_live, same_key_live = _read_live_handle_counts(cache_key)
        snapshot: dict[str, object] = {
            "event": event,
            "ts": time.time(),
            "request_id": get_request_id(),
            "pid": _safe_int(_safe_call(os.getpid)),
            "thread_id": _safe_int(_safe_call(threading.get_ident)),
            "task_id": _read_current_task_id(),
            "thread_count": _safe_int(_safe_call(threading.active_count)),
            "rss_bytes": _read_rss_bytes(),
            "win_handle_count": _read_windows_handle_count(),
            "file_cache_occupancy": _read_file_cache_occupancy(),
            "file_cache_maxsize": file_cache_maxsize(),
            "global_live_handles": global_live,
            "same_key_live_handles": same_key_live,
            "gc_counts": _read_gc_counts(),
        }
        if file_path is not None:
            snapshot["file_path"] = str(file_path)
        if cache_key is not None:
            snapshot["cache_key"] = cache_key
        if extra:
            for k, v in extra.items():
                snapshot[k] = v
        return snapshot
    except Exception:  # noqa: BLE001
        # Last-resort: the snapshot itself failed to build. Return a
        # minimal marker dict so the caller can still emit a line.
        return {"event": event, "ts": time.time(), "snapshot_error": True}


def _format_snapshot_line(snapshot: dict[str, object]) -> str:
    """Render ``snapshot`` as a single ``k=v k=v ."""
    parts: list[str] = []
    for k, v in snapshot.items():
        if v is None:
            parts.append(f"{k}=null")
        elif isinstance(v, tuple):
            parts.append(f"{k}=({','.join(str(x) for x in v)})")
        elif isinstance(v, bool):
            parts.append(f"{k}={'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            parts.append(f"{k}={v}")
        else:
            s = str(v)
            if " " in s or "=" in s or '"' in s:
                s = '"' + s.replace('"', '\\"') + '"'
            parts.append(f"{k}={s}")
    return " ".join(parts)


def _append_lifecycle_log(line: str) -> None:
    """Append one line to the persistent sink."""
    if _lifecycle_log_path is None:
        return
    try:
        with _lifecycle_log_lock:
            with _lifecycle_log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
    except Exception:  # noqa: BLE001
        # Diagnostic sink failures must be swallowed.
        pass


def emit_native_event(
    event: str,
    *,
    file_path: object | None = None,
    cache_key: object | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    """Emit a native_* event with a full snapshot."""
    try:
        snapshot = _capture_lifecycle_snapshot(
            event=event, file_path=file_path, cache_key=cache_key, extra=extra,
        )
        line = _format_snapshot_line(snapshot)
    except Exception:  # noqa: BLE001
        # Snapshot building itself failed; still try to emit a marker.
        line = (
            f"{event} ts={time.time()} request_id={get_request_id()} "
            f"snapshot_build_error=true"
        )
    try:
        logger.info(line)
    except Exception:  # noqa: BLE001
        pass
    _append_lifecycle_log(line)
    try:
        flush()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "file_cache_maxsize",
    "flush",
    "get_crash_log_path",
    "get_request_id",
    "request_context",
    "runtime_library_versions",
    "setup_diagnostics",
]
