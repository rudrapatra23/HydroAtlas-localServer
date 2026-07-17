from __future__ import annotations

from pathlib import Path, PurePosixPath


class FileService:
    """Local-path helper for the era5 netcdf cache."""

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
        """Return the per-variable cache root."""
        return self._cache_root

    def cache_path_for(
        self,
        provider: str,
        variable: str,
        year: int,
        month: int,
    ) -> Path:
        """Return the cache path for a single ``(provider, variable,."""
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
        """Create the parent directory for :meth:`cache_path_for` if."""
        cache_path = self.cache_path_for(provider, variable, year, month)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        return cache_path.parent


_SAFE_SEGMENT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _safe_segment(value: str, kind: str) -> str:
    """Return ``value`` if every character is in the safe-segment allowlist,."""
    if not value:
        raise ValueError(f"{kind} must be a non-empty string")
    if any(ch not in _SAFE_SEGMENT_CHARS for ch in value):
        raise ValueError(
            f"{kind} {value!r} contains characters outside "
            f"[A-Za-z0-9_-]; refused as a path segment"
        )
    return value
