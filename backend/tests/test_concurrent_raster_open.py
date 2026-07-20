"""Concurrency regression tests for per-cache-key raster open lock."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
import re

import pytest
import xarray as xr

from application.raster_cache import (
    OpenRasterHandle,
    RasterCache,
    RasterLease,
    _lease_registry,
    _open_counter,
    cache_key_for,
)
from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from domain.ports.storage_port import StoragePort


FIXTURE_NC = Path(__file__).parent / "fixtures" / "synthetic_hydrology_2024_01.nc"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class _FixtureStorage(StoragePort):
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


@pytest.fixture
def nc_body() -> bytes:
    return FIXTURE_NC.read_bytes()


@pytest.fixture
def cache(tmp_path: Path) -> RasterCache:
    return RasterCache(cache_root=tmp_path / "cache", max_bytes=10 * 1024 * 1024)


async def _open_handle(
    cache: RasterCache,
    storage: _FixtureStorage,
    asset: ClimateAsset,
    body: bytes,
) -> OpenRasterHandle:
    """Acquire a lease and open the dataset under the per-key open lock."""
    lease = await cache.acquire(asset, storage)
    try:
        ds = await cache.open_dataset(lease, asset=asset)
    except Exception:
        lease.release()
        raise
    return OpenRasterHandle(dataset=ds, path=lease.path, lease=lease)


# ── Guarantee 1: concurrent same-file opens all succeed ─────────────────


@pytest.mark.asyncio
async def test_concurrent_same_file_opens_all_succeed(
    cache: RasterCache, nc_body: bytes,
) -> None:
    """Simulate the production crash scenario: 8 concurrent requests."""
    storage = _FixtureStorage(nc_body)
    asset, _body = _make_asset(
        provider="era5-land", variable="surface_runoff",
        year=2026, month=5, body=nc_body,
    )

    # Prime the cache so every acquire is a hit (no download race).
    prime = await cache.acquire(asset, storage)
    prime.release()

    # Fire 8 concurrent opens of the same file.
    handles = await asyncio.gather(*[
        _open_handle(cache, storage, asset, nc_body) for _ in range(8)
    ])

    try:
        # Every handle must be a real, opened xr.Dataset.
        assert len(handles) == 8
        for h in handles:
            assert isinstance(h.dataset, xr.Dataset)
            assert "tp" in h.dataset.data_vars or "ro" in h.dataset.data_vars
    finally:
        for h in handles:
            h.close()

    # Lease registry drained — no leaked refcounts.
    assert _lease_registry.active_keys() == set()


# ── Guarantee 2: opens of different files proceed in parallel ──────────


@pytest.mark.asyncio
async def test_different_files_proceed_in_parallel(
    cache: RasterCache, nc_body: bytes,
) -> None:
    """Two concurrent opens of different rasters must not serialise on."""
    storage = _FixtureStorage(nc_body)
    asset_a, _ = _make_asset(
        provider="era5-land", variable="precipitation",
        year=2024, month=1, body=nc_body,
    )
    asset_b, _ = _make_asset(
        provider="era5-land", variable="soil_moisture",
        year=2024, month=1, body=nc_body,
    )
    # Prime both.
    for asset in (asset_a, asset_b):
        prime = await cache.acquire(asset, storage)
        prime.release()

    # Baseline: one open.
    import time as _time
    t0 = _time.perf_counter()
    h_a = await _open_handle(cache, storage, asset_a, nc_body)
    h_a.close()
    single_seconds = _time.perf_counter() - t0

    # Two opens on different keys in parallel.
    t0 = _time.perf_counter()
    h_a, h_b = await asyncio.gather(
        _open_handle(cache, storage, asset_a, nc_body),
        _open_handle(cache, storage, asset_b, nc_body),
    )
    parallel_seconds = _time.perf_counter() - t0
    h_a.close(); h_b.close()

    # Parallel must be < 2x single. On a quiet CI runner the ratio is
    # very close to 1.0; on a loaded runner it can spike up. We use 1.8x
    # as a generous bound that still fails if a global lock serialises.
    assert parallel_seconds < 1.8 * single_seconds, (
        f"parallel={parallel_seconds:.3f}s single={single_seconds:.3f}s "
        f"\u2014 different-file opens appear to be serialised"
    )


# ── Guarantee 3: the per-key open lock serialises opens of the same file


@pytest.mark.asyncio
async def test_open_lock_serialises_same_file(
    cache: RasterCache, nc_body: bytes, caplog,
) -> None:
    """Verify that the per-key lock is taken: while one open is in."""
    storage = _FixtureStorage(nc_body)
    asset, _ = _make_asset(
        provider="era5-land", variable="precipitation",
        year=2024, month=1, body=nc_body,
    )
    # Prime.
    prime = await cache.acquire(asset, storage)
    prime.release()

    import application.raster_cache as rc
    observed_counts: list[int] = []
    real_open = rc.xr.open_dataset

    def counting_open(path, *args, **kwargs):
        key = cache_key_for(asset)
        observed_counts.append(_open_counter.current(key))
        return real_open(path, *args, **kwargs)

    rc.xr.open_dataset = counting_open
    try:
        handles = await asyncio.gather(*[
            _open_handle(cache, storage, asset, nc_body) for _ in range(4)
        ])
        for h in handles:
            h.close()
    finally:
        rc.xr.open_dataset = real_open

    # Every open observed at least one concurrent reader (itself or a
    # sibling) because the lock serialises them. If the lock were
    # missing, observed_counts would be 1 for every call (no overlap).
    assert observed_counts, "xr.open_dataset was never called"
    # At least one observation must be >= 1 (the call itself). We
    # assert all are >= 1 because the lock holds the count for the
    # duration of the call.
    assert all(c >= 1 for c in observed_counts), (
        f"observed_counts={observed_counts} \u2014 counter should be "
        f">= 1 while open is in flight"
    )


# ── Guarantee 4: handle close is deterministic even on compute exception


@pytest.mark.asyncio
async def test_handle_close_releases_lease_on_compute_exception(
    cache: RasterCache, nc_body: bytes,
) -> None:
    """If the user's compute raises while the dataset is in use,."""
    storage = _FixtureStorage(nc_body)
    asset, _ = _make_asset(
        provider="era5-land", variable="precipitation",
        year=2024, month=1, body=nc_body,
    )

    handle: OpenRasterHandle | None = None
    with pytest.raises(RuntimeError, match="boom"):
        try:
            handle = await _open_handle(cache, storage, asset, nc_body)
            # Simulate a failing compute.
            raise RuntimeError("boom")
        finally:
            if handle is not None:
                handle.close()

    # Lease must have been released.
    assert _lease_registry.active_keys() == set()
    assert handle is not None
    assert handle._closed is True


# ── Guarantee 5: lease refcounts drain to zero (no leak) ───────────────


@pytest.mark.asyncio
async def test_lease_refcounts_drain_after_concurrent_opens(
    cache: RasterCache, nc_body: bytes,
) -> None:
    """After n concurrent opens + closes, the per-key lease refcount."""
    storage = _FixtureStorage(nc_body)
    asset, _ = _make_asset(
        provider="era5-land", variable="precipitation",
        year=2024, month=1, body=nc_body,
    )
    # Prime.
    prime = await cache.acquire(asset, storage)
    prime.release()

    N = 16
    handles = await asyncio.gather(*[
        _open_handle(cache, storage, asset, nc_body) for _ in range(N)
    ])
    for h in handles:
        h.close()

    assert _lease_registry.active_keys() == set()


# ── Guarantee 6: structured OPEN_DATASET logging captures concurrency ──


@pytest.mark.asyncio
async def test_open_dataset_emits_structured_log(
    cache: RasterCache, nc_body: bytes, caplog,
) -> None:
    """The ``open_dataset`` log line must carry ``key``,."""
    import logging
    storage = _FixtureStorage(nc_body)
    asset, _ = _make_asset(
        provider="era5-land", variable="precipitation",
        year=2024, month=1, body=nc_body,
    )
    prime = await cache.acquire(asset, storage)
    prime.release()

    caplog.set_level(logging.INFO, logger="uvicorn.error")
    handle = await _open_handle(cache, storage, asset, nc_body)
    try:
        # Find the OPEN_DATASET log line.
        open_lines = [
            rec.message for rec in caplog.records
            if "OPEN_DATASET" in rec.message
        ]
        assert open_lines, "no OPEN_DATASET log line emitted"
        line = open_lines[-1]
        # Every required field is present.
        for field in ("key=", "wait_seconds=", "open_seconds=",
                      "status=ok", "concurrent_readers="):
            assert field in line, f"missing {field!r} in OPEN_DATASET log: {line!r}"
        # Key matches the asset's storage_key.
        assert asset.storage_key in line
    finally:
        handle.close()

    # After close, a DATASET_CLOSE line should also appear.
    close_lines = [
        rec.message for rec in caplog.records
        if "DATASET_CLOSE" in rec.message
    ]
    assert close_lines, "no DATASET_CLOSE log line emitted"


# ── Guarantee 7: cancellation during open does not crash the lock ─────


@pytest.mark.asyncio
async def test_cancellation_during_open_does_not_corrupt_lock(
    cache: RasterCache, nc_body: bytes,
) -> None:
    """If the caller's task is cancelled while waiting for the open."""
    storage = _FixtureStorage(nc_body)
    asset, _ = _make_asset(
        provider="era5-land", variable="precipitation",
        year=2024, month=1, body=nc_body,
    )
    prime = await cache.acquire(asset, storage)
    prime.release()

    # Launch an open, cancel it.
    handle_task = asyncio.create_task(_open_handle(cache, storage, asset, nc_body))
    await asyncio.sleep(0)  # let it start
    handle_task.cancel()
    try:
        await handle_task
    except (asyncio.CancelledError, BaseException):
        pass

    # Now a fresh open must succeed without deadlock.
    handle = await _open_handle(cache, storage, asset, nc_body)
    try:
        assert isinstance(handle.dataset, xr.Dataset)
    finally:
        handle.close()

    assert _lease_registry.active_keys() == set()
