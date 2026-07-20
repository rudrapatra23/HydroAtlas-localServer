"""Regression tests for the ``openrasterhandle`` lease lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import pytest
import xarray as xr

from application.raster_cache import (
    OpenRasterHandle,
    RasterCache,
    RasterLease,
    _lease_registry,
    _maybe_sweep_cache,
    cache_key_for,
)
from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from domain.ports.storage_port import StoragePort


FIXTURE_NC = Path(__file__).parent / "fixtures" / "synthetic_hydrology_2024_01.nc"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class _FixtureStorage(StoragePort):
    """Storage port that hands out the synthetic fixture for every key."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self.download_calls: list[str] = []

    def upload(self, key, data) -> None:
        return None

    def download(self, key: str, target) -> None:
        self.download_calls.append(key)
        if isinstance(target, Path):
            target.write_bytes(self._body)
        else:
            target.write(self._body)

    def exists(self, key: str) -> bool:
        return True

    def delete(self, key: str) -> None:
        return None

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return f"https://example/{key}?exp={expires_in}"

    def list(self, prefix: str = "") -> Sequence[str]:
        return []


def _make_asset(
    *,
    provider: str = "era5-land",
    variable: str = "precipitation",
    year: int = 2024,
    month: int = 1,
    body: bytes | None = None,
) -> tuple[ClimateAsset, bytes]:
    if body is None:
        body = FIXTURE_NC.read_bytes()
    storage_key = f"{provider}/{variable}/{year:04d}/{month:02d}.nc"
    asset = ClimateAsset(
        id=f"{provider}-{variable}-{year:04d}-{month:02d}",
        provider=provider, variable=variable, year=year, month=month,
        storage_key=storage_key, checksum=_sha(body),
        file_size=len(body), status=ClimateAssetStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    return asset, body


def _open_with_handle(
    cache: RasterCache, storage: _FixtureStorage, asset: ClimateAsset,
) -> OpenRasterHandle:
    """Helper: build an openrasterhandle the way ``read_raster_from_s3``."""
    lease = asyncio.get_event_loop().run_until_complete(
        cache.acquire(asset, storage)
    ) if False else _acquire_sync(cache, storage, asset)
    rds = xr.open_dataset(lease.path, engine="netcdf4")
    return OpenRasterHandle(dataset=rds, path=lease.path, lease=lease)


async def _acquire_sync(cache: RasterCache, storage: _FixtureStorage, asset: ClimateAsset) -> RasterLease:
    """Async wrapper used by ``_open_with_handle``."""
    return await cache.acquire(asset, storage)


@pytest.fixture
def nc_body() -> bytes:
    return FIXTURE_NC.read_bytes()


@pytest.fixture
def cache(tmp_path: Path) -> RasterCache:
    return RasterCache(cache_root=tmp_path / "cache", max_bytes=10 * 1024 * 1024)


# ── Guarantee 1: no arbitrary attributes on the returned Dataset ────────


@pytest.mark.asyncio
async def test_handle_carries_no_arbitrary_attributes_on_dataset(
    cache: RasterCache, nc_body: bytes,
) -> None:
    """``openrasterhandle."""
    storage = _FixtureStorage(nc_body)
    asset, _body = _make_asset(body=nc_body)

    lease = await cache.acquire(asset, storage)
    try:
        rds = xr.open_dataset(lease.path, engine="netcdf4")
    except Exception:
        lease.release()
        raise
    handle = OpenRasterHandle(dataset=rds, path=lease.path, lease=lease)
    try:
        # Handle shape: dataset + path + lease, NOT a tuple.
        assert isinstance(handle, OpenRasterHandle)
        assert isinstance(handle.dataset, xr.Dataset)

        # No arbitrary attribute named like the old lease slot.
        assert not hasattr(handle.dataset, "_raster_lease")
        # ``dataset.attrs`` is scientific metadata only; nothing leaked
        # into it from the cache layer.
        for forbidden in ("_raster_lease", "_lease", "lease", "_cache_key"):
            assert forbidden not in handle.dataset.attrs, (
                f"{forbidden!r} leaked into dataset.attrs"
            )
        # No variable named like the lease either.
        for forbidden in ("_raster_lease", "_lease"):
            assert forbidden not in handle.dataset.data_vars
        # The dataset must still be usable as a normal xr.Dataset.
        assert "tp" in handle.dataset.data_vars
    finally:
        handle.close()


# ── Guarantee 2: lease active while dataset in use ──────────────────────


@pytest.mark.asyncio
async def test_lease_active_while_handle_in_use(
    cache: RasterCache, nc_body: bytes,
) -> None:
    """The per-key refcount must be 1 while the handle is open."""
    storage = _FixtureStorage(nc_body)
    asset, _body = _make_asset(body=nc_body)
    key = cache_key_for(asset)
    assert _lease_registry.active_keys() == set()

    lease = await cache.acquire(asset, storage)
    try:
        rds = xr.open_dataset(lease.path, engine="netcdf4")
    except Exception:
        lease.release()
        raise
    handle = OpenRasterHandle(dataset=rds, path=lease.path, lease=lease)
    try:
        # Refcount incremented while the dataset is in use.
        assert _lease_registry.active_keys() == {key}
        # Sanity: the dataset is a real, opened xr.Dataset.
        assert isinstance(handle.dataset, xr.Dataset)
        assert "tp" in handle.dataset.data_vars
    finally:
        handle.close()

    # After close, the refcount drops back to zero.
    assert _lease_registry.active_keys() == set()


# ── Guarantee 3: lease releases on close ────────────────────────────────


@pytest.mark.asyncio
async def test_handle_close_releases_lease_and_is_idempotent(
    cache: RasterCache, nc_body: bytes,
) -> None:
    """``handle."""
    storage = _FixtureStorage(nc_body)
    asset, _body = _make_asset(body=nc_body)

    lease = await cache.acquire(asset, storage)
    try:
        rds = xr.open_dataset(lease.path, engine="netcdf4")
    except Exception:
        lease.release()
        raise
    handle = OpenRasterHandle(dataset=rds, path=lease.path, lease=lease)

    handle.close()
    # After the first close, the registry is empty.
    assert _lease_registry.active_keys() == set()

    # Second close is a no-op.
    handle.close()
    assert _lease_registry.active_keys() == set()

    # Async context manager entry returns the handle.
    lease2 = await cache.acquire(asset, storage)
    try:
        rds2 = xr.open_dataset(lease2.path, engine="netcdf4")
    except Exception:
        lease2.release()
        raise
    handle2 = OpenRasterHandle(dataset=rds2, path=lease2.path, lease=lease2)
    async with handle2 as h:
        assert h is handle2
        assert _lease_registry.active_keys() == {cache_key_for(asset)}
    # Async context manager exit releases the lease.
    assert _lease_registry.active_keys() == set()


# ── Guarantee 4: lease releases on compute exception ────────────────────


@pytest.mark.asyncio
async def test_lease_released_when_compute_raises(
    cache: RasterCache, nc_body: bytes,
) -> None:
    """If the user's compute raises while the dataset is in use,."""
    storage = _FixtureStorage(nc_body)
    asset, _body = _make_asset(body=nc_body)
    key = cache_key_for(asset)

    handle: OpenRasterHandle | None = None
    with pytest.raises(RuntimeError, match="boom"):
        try:
            lease = await cache.acquire(asset, storage)
            try:
                rds = xr.open_dataset(lease.path, engine="netcdf4")
            except Exception:
                lease.release()
                raise
            handle = OpenRasterHandle(dataset=rds, path=lease.path, lease=lease)
            # Simulate user compute that fails.
            raise RuntimeError("boom")
        finally:
            # This is the exact pattern used in every production caller.
            if handle is not None:
                handle.close()

    # Lease must be released even though the compute raised.
    assert _lease_registry.active_keys() == set()
    # And the handle must be marked closed.
    assert handle is not None
    assert handle._closed is True


@pytest.mark.asyncio
async def test_lease_released_when_open_dataset_raises(
    cache: RasterCache, nc_body: bytes, monkeypatch,
) -> None:
    """If ``xr."""
    storage = _FixtureStorage(nc_body)
    asset, _body = _make_asset(body=nc_body)
    key = cache_key_for(asset)

    def boom(*args, **kwargs):
        raise OSError("simulated netcdf open failure")

    monkeypatch.setattr("xarray.open_dataset", boom)

    handle: OpenRasterHandle | None = None
    with pytest.raises(OSError):
        try:
            lease = await cache.acquire(asset, storage)
            try:
                rds = xr.open_dataset(lease.path, engine="netcdf4")
            except Exception:
                lease.release()
                raise
            handle = OpenRasterHandle(dataset=rds, path=lease.path, lease=lease)
        finally:
            if handle is not None:
                handle.close()

    # The lease acquired before the failure must be released.
    assert _lease_registry.active_keys() == set()


# ── Guarantee 5: active cache file cannot be evicted during use ─────────


@pytest.mark.asyncio
async def test_active_cache_file_not_evicted_while_lease_held(
    cache: RasterCache, nc_body: bytes,
) -> None:
    """While a handle is open, the eviction sweep must refuse to unlink."""
    storage = _FixtureStorage(nc_body)
    asset, _body = _make_asset(body=nc_body)

    lease = await cache.acquire(asset, storage)
    try:
        rds = xr.open_dataset(lease.path, engine="netcdf4")
    except Exception:
        lease.release()
        raise
    handle = OpenRasterHandle(dataset=rds, path=lease.path, lease=lease)
    try:
        cache_path = handle.path
        assert cache_path.exists()

        # Force an eviction pass with a budget smaller than the cache
        # file. The active key must be in the skip set, so the file is
        # NOT unlinked.
        active = _lease_registry.active_keys()
        _maybe_sweep_cache(
            cache_root=cache.cache_root,  # type: ignore[arg-type]
            max_bytes=1,  # pathologically small budget
            active_keys=active,
        )
        assert cache_path.exists(), "active cache file was evicted under a held lease"
    finally:
        handle.close()

    # After close, with the same pathologically small budget, the
    # eviction sweep SHOULD remove the file.
    _maybe_sweep_cache(
        cache_root=cache.cache_root,  # type: ignore[arg-type]
        max_bytes=1,
        active_keys=_lease_registry.active_keys(),
    )
    assert not cache_path.exists(), "idle cache file was not evicted when budget exceeded"
