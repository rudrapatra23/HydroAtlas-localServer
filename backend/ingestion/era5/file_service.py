from __future__ import annotations

from pathlib import Path, PurePosixPath


class FileService:
    """Local-path helper for the era5 NetCDF cache.

    Owns two layouts under the same ``storage_root``:

    - **Bundle / temp layout** (legacy, used by the in-progress CDS download
      step): ``storage_root/{YYYY}/hydrology_{YYYY}_{MM}.nc`` and
      ``storage_root/tmp/...``. Retained for the synchronous CDS path inside
      :class:`Downloader`; a fresh download still produces a single bundle
      NetCDF that the splitter then fans out into per-variable files.
    - **Per-variable cache layout** (PostgreSQL-as-source-of-truth): a
      downloaded-from-S3 mirror at
      ``storage_root/cache/{provider}/{variable}/{YYYY}/{MM}.nc``. This is
      the cache that :meth:`Downloader.ensure_dataset` reads from and writes
      to. It is purely transient — deleting it (or any subset) is safe; the
      next call will re-download from S3 if the PostgreSQL row still exists,
      or fall through to CDS only if the row is missing.

    The two layouts are kept independent so a partial cache wipe can never
    confuse the in-progress download path into thinking the bundle is
    already on disk.
    """

    def __init__(self, storage_root: Path, temp_dir: Path) -> None:
        self._storage_root = storage_root.resolve()
        self._temp_dir = temp_dir.resolve()
        self._cache_root = (self._storage_root / "cache").resolve()
        self._storage_root.mkdir(parents=True, exist_ok=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._cache_root.mkdir(parents=True, exist_ok=True)

    # ── Bundle / temp layout (in-progress CDS download)

    def filename_for(self, year: int, month: int) -> str:
        return PurePosixPath(
            f"{year:04d}", f"hydrology_{year:04d}_{month:02d}.nc"
        ).as_posix()

    def temp_path_for(self, year: int, month: int) -> Path:
        return self._temp_dir / f"hydrology_{year:04d}_{month:02d}.nc.tmp"

    def path_for_filename(self, filename: str) -> Path:
        relative_path = Path(*PurePosixPath(filename).parts)
        path = (self._storage_root / relative_path).resolve()
        if not path.is_relative_to(self._storage_root):
            raise ValueError("invalid storage filename")
        return path

    def delete(self, filename: str) -> bool:
        path = self.path_for_filename(filename)
        if not path.exists():
            return False
        path.unlink()
        return True

    def size(self, filename: str) -> int:
        return self.path_for_filename(filename).stat().st_size

    # ── Per-variable cache layout

    @property
    def cache_root(self) -> Path:
        """Return the per-variable cache root
        (``storage_root/cache``). Created eagerly in ``__init__``.
        """
        return self._cache_root

    def cache_path_for(
        self,
        provider: str,
        variable: str,
        year: int,
        month: int,
    ) -> Path:
        """Return the cache path for a single ``(provider, variable,
        year, month)`` tuple. Does not touch the filesystem; pair with
        :meth:`ensure_cache_dir` to create the directory before writing.

        Layout: ``{storage_root}/cache/{provider}/{variable}/{YYYY}/{MM}.nc``.

        Path segments are validated to refuse traversal — both ``provider``
        and ``variable`` are constrained to ``[A-Za-z0-9_-]+`` so a caller
        can never escape the cache root via a crafted variable name.
        """
        provider_seg = _safe_segment(provider, "provider")
        variable_seg = _safe_segment(variable, "variable")
        relative = PurePosixPath(
            provider_seg, variable_seg, f"{year:04d}", f"{month:02d}.nc"
        )
        path = (self._cache_root / relative).resolve()
        if not path.is_relative_to(self._cache_root):
            raise ValueError(
                f"cache path {path} escapes cache root {self._cache_root}"
            )
        return path

    def ensure_cache_dir(
        self,
        provider: str,
        variable: str,
        year: int,
        month: int,
    ) -> Path:
        """Create the parent directory for :meth:`cache_path_for` if
        missing, and return it. The NetCDF file itself is *not* created;
        callers decide whether to download, upload, or just resolve.
        """
        cache_path = self.cache_path_for(provider, variable, year, month)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        return cache_path.parent


_SAFE_SEGMENT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _safe_segment(value: str, kind: str) -> str:
    """Return ``value`` if every character is in the safe-segment allowlist,
    otherwise raise ``ValueError``. Refuses empty strings and traversal
    attempts (``..``, ``/``, ``\\``).
    """
    if not value:
        raise ValueError(f"{kind} must be a non-empty string")
    if any(ch not in _SAFE_SEGMENT_CHARS for ch in value):
        raise ValueError(
            f"{kind} {value!r} contains characters outside "
            f"[A-Za-z0-9_-]; refused as a path segment"
        )
    return value
