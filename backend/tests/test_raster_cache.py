"""Verification tests for ``application."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
import hashlib

import pytest

from application.raster_cache import (
    DEFAULT_RASTER_CACHE_MAX_BYTES,
    RasterCache,
    RasterLease,
    _LockRegistry,
    _cache_size_bytes,
    _lease_registry,
    _lock_registry,
    _maybe_sweep_cache,
    _publish_with_sidecar,
    _sidecar_path,
    _validate_fast,
    cache_key_for,
    cache_path_for,
)
from core.config import get_settings
from application.raster_computation import RasterComputation
from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from domain.ports.storage_port import StoragePort
import xarray as xr
import numpy as np
import geopandas as gpd
import shapely.geometry

from ingestion.era5.checksums import sha256_file


# Real NetCDF fixture for raster-grid extraction tests. The fixture
# has valid ``tp`` data over a bounded lon/lat grid so ``rio.clip``
# and ``_extract_raster_grid`` exercise the real coordinate paths.
_FIXTURE_NC_PATH = Path(__file__).parent / "fixtures" / "synthetic_hydrology_2024_01.nc"
FIXTURE_NC_BODY = _FIXTURE_NC_PATH.read_bytes()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_asset(
    *,
    provider: str = "era5-land",
    variable: str = "precipitation",
    year: int = 2025,
    month: int = 7,
    storage_key: str | None = None,
    body: bytes = b"x" * 1024,
) -> tuple[ClimateAsset, bytes]:
    if storage_key is None:
        storage_key = f"{provider}/{variable}/{year:04d}/{month:02d}.nc"
    return ClimateAsset(
        id=f"{provider}-{variable}-{year:04d}-{month:02d}",
        provider=provider, variable=variable, year=year, month=month,
        storage_key=storage_key, checksum=_sha(body),
        file_size=len(body), status=ClimateAssetStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ), body


class _FakeStorage(StoragePort):
    """In-memory storage port that records every download call."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.download_calls: list[str] = []
        self.upload_calls: list[tuple[str, int]] = []

    def seed(self, key: str, body: bytes) -> None:
        self.objects[key] = body

    def upload(self, key, data) -> None:
        if isinstance(data, Path):
            self.objects[key] = data.read_bytes()
        elif isinstance(data, bytes):
            self.objects[key] = data
        else:
            self.objects[key] = data.read()
        self.upload_calls.append((key, len(self.objects[key])))

    def download(self, key: str, target) -> None:
        self.download_calls.append(key)
        body = self.objects.get(key)
        if body is None:
            raise KeyError(key)
        if isinstance(target, Path):
            target.write_bytes(body)
        else:
            target.write(body)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return f"https://example/{key}?exp={expires_in}"

    def list(self, prefix: str = "") -> Sequence[str]:
        return [k for k in self.objects if k.startswith(prefix)]


@pytest.fixture
def cache(tmp_path: Path) -> RasterCache:
    return RasterCache(cache_root=tmp_path / "cache", max_bytes=10 * 1024 * 1024)


def test_default_cache_root_uses_settings_not_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "canonical-storage"
    expected_cache_root = storage_root / "cache"

    monkeypatch.setenv("APP_NAME", "test")
    monkeypatch.setenv("VERSION", "0.0.0")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("ERA5_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("RASTER_CACHE_MAX_BYTES", "4096")
    get_settings.cache_clear()

    first_cwd = tmp_path / "launch-from-backend"
    second_cwd = tmp_path / "launch-from-root"
    first_cwd.mkdir()
    second_cwd.mkdir()

    try:
        monkeypatch.chdir(first_cwd)
        first = RasterCache()
        monkeypatch.chdir(second_cwd)
        second = RasterCache()
    finally:
        get_settings.cache_clear()

    assert first.cache_root == expected_cache_root
    assert second.cache_root == expected_cache_root
    assert first.cache_root == second.cache_root
    assert first.max_bytes == 4096
    assert second.max_bytes == 4096


# ── Correction 1: real lease lifecycle ───────────────────────────────────


@pytest.mark.asyncio
async def test_lease_release_is_real_no_double_release(tmp_path: Path) -> None:
    """Acquiring a lease increments the per-key count; releasing."""
    cache = RasterCache(cache_root=tmp_path / "cache", max_bytes=10 * 1024 * 1024)
    storage = _FakeStorage()
    asset, body = _make_asset(body=b"y" * 2048)
    storage.seed(asset.storage_key, body)

    assert _lease_registry.active_keys() == set()
    lease = await cache.acquire(asset, storage)
    assert _lease_registry.active_keys() == {cache_key_for(asset)}

    lease.release()
    assert _lease_registry.active_keys() == set()

    # Double release is a no-op.
    lease.release()
    assert _lease_registry.active_keys() == set()


# ── Correction 2: max_bytes=0 genuinely disables persistent caching ────


@pytest.mark.asyncio
async def test_max_bytes_zero_disables_on_disk_cache(tmp_path: Path) -> None:
    """When ``max_bytes == 0``, no files are written under."""
    cache = RasterCache(cache_root=tmp_path / "cache", max_bytes=0)
    storage = _FakeStorage()
    asset, body = _make_asset(body=b"z" * 4096)
    storage.seed(asset.storage_key, body)

    assert cache.on_disk_enabled is False
    assert cache.cache_root is None

    lease = await cache.acquire(asset, storage)
    # Path is somewhere in the OS tmp dir, NOT under our cache root.
    assert "era5/cache" not in str(lease.path)
    assert lease.cache_hit is False
    assert lease.path.exists()

    # No file under our cache root.
    assert not list((tmp_path / "cache").rglob("*.nc"))
    # No sidecar under our cache root either.
    assert not list((tmp_path / "cache").rglob("*.fp"))

    lease.release()
    # Ephemeral tempfile is cleaned up on release.
    assert not lease.path.exists()
    # Still nothing under the cache root.
    assert not list((tmp_path / "cache").rglob("*.nc"))


@pytest.mark.asyncio
async def test_max_bytes_zero_two_concurrent_share_lock_only(tmp_path: Path) -> None:
    """``max_bytes == 0`` still serialises concurrent same-key."""
    cache = RasterCache(cache_root=tmp_path / "cache", max_bytes=0)
    storage = _FakeStorage()
    asset, body = _make_asset(body=b"x" * 1024)
    storage.seed(asset.storage_key, body)

    leases = await asyncio.gather(
        cache.acquire(asset, storage),
        cache.acquire(asset, storage),
        cache.acquire(asset, storage),
    )
    # Three acquires => three downloads (no in-memory handoff in
    # ephemeral mode by design).
    assert storage.download_calls.count(asset.storage_key) == 3

    # But all three tempfiles are valid and distinct.
    paths = {l.path for l in leases}
    assert len(paths) == 3

    for lease in leases:
        lease.release()


# ── Correction 3: sidecar fingerprint fast path (no full SHA on hit) ────


@pytest.mark.asyncio
async def test_fingerprint_fast_path_skips_full_sha_on_cache_hit(
    cache: RasterCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a cache hit, the hot path reads only the first 64 kib and."""
    storage = _FakeStorage()
    asset, body = _make_asset(body=b"a" * 4096)
    storage.seed(asset.storage_key, body)

    # First acquire: full SHA on download (one time, necessary).
    first = await cache.acquire(asset, storage)
    first.release()

    # Spy on sha256_file to count full-file hashes. The hot-path
    # validate must NOT call it.
    from application import raster_cache as rc
    real_sha256_file = rc.sha256_file
    call_count = {"n": 0}

    def counting(path: Path) -> str:
        call_count["n"] += 1
        return real_sha256_file(path)

    monkeypatch.setattr(rc, "sha256_file", counting)

    second = await cache.acquire(asset, storage)
    assert second.cache_hit is True
    # Hot path did NOT hash the full file.
    assert call_count["n"] == 0
    second.release()


# ── Correction 4: bounded LRU lock registry ──────────────────────────────


def test_lock_registry_is_bounded_lru() -> None:
    """The module-level lock registry must not grow past."""
    from application.raster_cache import _MAX_LOCK_REGISTRY_ENTRIES

    reg = _LockRegistry()
    # Fill it past capacity.
    for i in range(_MAX_LOCK_REGISTRY_ENTRIES + 50):
        reg.get_or_create((f"p{i}", "v", 2025, 1))
    assert len(reg) <= _MAX_LOCK_REGISTRY_ENTRIES
    # The earliest keys should be gone.
    assert ("p0", "v", 2025, 1) not in reg._data
    # The most-recently-used keys should still be present.
    assert (f"p{_MAX_LOCK_REGISTRY_ENTRIES + 49}", "v", 2025, 1) in reg._data


def test_lock_registry_lru_reorders_on_touch() -> None:
    reg = _LockRegistry()
    a = reg.get_or_create(("p", "v", 2025, 1))
    b = reg.get_or_create(("p", "v", 2025, 2))
    # Touch a, making it most-recently-used.
    reg.get_or_create(("p", "v", 2025, 1))
    # Now b is the oldest. We can't easily force eviction without
    # filling to capacity; instead, verify the order directly.
    assert next(iter(reg._data)) == ("p", "v", 2025, 2)
    assert a is reg.get_or_create(("p", "v", 2025, 1))  # same instance


# ── Correction 5: event-loop-safe single-flight ──────────────────────────


@pytest.mark.asyncio
async def test_lock_registry_creates_fresh_lock_per_loop() -> None:
    """The lock registry must key locks by the running loop's identity."""
    import asyncio as _aio

    reg = _LockRegistry()
    key = ("era5-land", "precipitation", 2025, 7)

    lock_a = reg.get_or_create(key)
    current_loop_id = id(_aio.get_running_loop())

    # The stored entry must be tagged with the current loop id.
    stored_loop_id, stored_lock = next(iter(reg._data.values()))
    assert stored_loop_id == current_loop_id
    assert stored_lock is lock_a

    # A second call from the same loop returns the same lock instance.
    lock_a_again = reg.get_or_create(key)
    assert lock_a_again is lock_a

    # Simulate "different loop" by patching the module-level
    # loop-id resolver to report a sentinel. The registry must then
    # allocate a fresh lock rather than return loop 1's lock.
    from application import raster_cache as rc_mod
    sentinel_id = current_loop_id + 999_999
    original = rc_mod._current_loop_id
    try:
        rc_mod._current_loop_id = lambda: sentinel_id
        lock_b = reg.get_or_create(key)
        assert lock_b is not lock_a
    finally:
        rc_mod._current_loop_id = original


# ── Correction 6: eviction skips active leases ──────────────────────────


@pytest.mark.asyncio
async def test_eviction_does_not_remove_files_held_by_active_lease(
    tmp_path: Path,
) -> None:
    """While a lease is outstanding, the eviction sweep must skip the."""
    cache = RasterCache(cache_root=tmp_path / "cache", max_bytes=512)
    storage = _FakeStorage()
    asset, body = _make_asset(body=b"c" * 1024)
    storage.seed(asset.storage_key, body)

    lease = await cache.acquire(asset, storage)
    # Touch the file so its atime is recent.
    cache_path = lease.path
    cache_path.touch()

    # Force an eviction sweep with a tiny budget while the lease is held.
    _maybe_sweep_cache(
        cache.cache_root,
        max_bytes=512,
        active_keys={cache_key_for(asset)},
    )

    # File must still be on disk because lease is active.
    assert cache_path.exists()

    # Release the lease; the file is now eligible for eviction.
    lease.release()
    _maybe_sweep_cache(
        cache.cache_root,
        max_bytes=512,
        active_keys=set(),
    )
    # File is gone — it was over budget and unprotected.
    assert not cache_path.exists()


# ── Brief Scenario 1: sequential reuse ──────────────────────────────────


@pytest.mark.asyncio
async def test_sequential_acquire_reuses_cache(cache: RasterCache) -> None:
    storage = _FakeStorage()
    asset, body = _make_asset(body=b"d" * 2048)
    storage.seed(asset.storage_key, body)

    first = await cache.acquire(asset, storage)
    first.release()
    second = await cache.acquire(asset, storage)
    second.release()

    assert storage.download_calls.count(asset.storage_key) == 1
    # The path is identical across both acquires.
    assert first.path == second.path


# ── Raster grid extraction tests ────────────────────────────────────────


def _make_2d_netcdf_body(
    *,
    values: np.ndarray | None = None,
    lon_range: tuple[float, float] = (76.5, 78.5),
    lat_range: tuple[float, float] = (11.5, 13.5),
    n_lon: int = 5,
    n_lat: int = 5,
) -> bytes:
    """Build a small 2d ``(lat, lon)`` netcdf body in-memory."""
    import tempfile

    lon = np.linspace(lon_range[0], lon_range[1], n_lon)
    lat = np.linspace(lat_range[0], lat_range[1], n_lat)
    if values is None:
        values = np.ones((n_lat, n_lon), dtype="float64") * 0.001
    ds = xr.Dataset(
        {"tp": (("lat", "lon"), values)},
        coords={"lat": lat, "lon": lon},
    )
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as fh:
        path = Path(fh.name)
    try:
        ds.to_netcdf(path, engine="netcdf4")
        return path.read_bytes()
    finally:
        try:
            path.unlink()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_extract_raster_grid_clip_and_cell_count(
    cache: RasterCache, tmp_path: Path
) -> None:
    """_extract_raster_grid returns one cell per valid pixel after."""
    storage = _FakeStorage()
    body = _make_2d_netcdf_body()
    asset, body = _make_asset(body=body)
    storage.seed(asset.storage_key, body)

    # Acquire the raster, then extract the grid. ``cache.acquire``
    # only returns a lease for the on-disk path — it does NOT open
    # the dataset — so the direct ``xr.open_dataset`` below is the
    # only open for this test and there is no file-cache clash with
    # the acquire path.
    computation = RasterComputation(repository=None, storage=storage, raster_cache=cache)
    lease = await cache.acquire(asset, storage)
    try:
        rds = xr.open_dataset(lease.path, engine="netcdf4")
        try:
            data = rds["tp"].rio.write_crs("EPSG:4326")
            data = data.rio.set_spatial_dims(x_dim="lon", y_dim="lat")
            geometry = gpd.GeoDataFrame(
                {
                    "district_id": ["test"],
                    "geometry": [shapely.geometry.box(77, 12, 78, 13)],
                },
                crs="EPSG:4326",
            )
            result = computation._extract_raster_grid(
                data,
                geometry,
                district_id="test",
                variable="precipitation",
                year=asset.year,
                month=asset.month,
            )
            assert result is not None
            assert len(result.cells) > 0
            assert all(c.value > 0 for c in result.cells)
            assert all(c.lon > 0 for c in result.cells)
            # Every cell must have a non-zero, ordered bounding box.
            for cell in result.cells:
                assert cell.min_lon < cell.max_lon
                assert cell.min_lat < cell.max_lat
                assert cell.max_lon - cell.min_lon > 0
                assert cell.max_lat - cell.min_lat > 0
        finally:
            rds.close()
    finally:
        lease.release()


@pytest.mark.asyncio
async def test_extract_raster_grid_filters_inf_and_nan(
    cache: RasterCache, tmp_path: Path
) -> None:
    """_extract_raster_grid excludes nan and infinity values from its."""
    values = np.ones((5, 5), dtype="float64") * 0.001
    # Scatter NaN and ±Inf across the grid; only finite cells must survive.
    values[0, 0] = np.nan
    values[2, 2] = np.inf
    values[4, 4] = -np.inf
    storage = _FakeStorage()
    body = _make_2d_netcdf_body(values=values)
    asset, body = _make_asset(body=body)
    storage.seed(asset.storage_key, body)

    computation = RasterComputation(repository=None, storage=storage, raster_cache=cache)
    lease = await cache.acquire(asset, storage)
    try:
        rds = xr.open_dataset(lease.path, engine="netcdf4")
        try:
            data = rds["tp"].rio.write_crs("EPSG:4326")
            data = data.rio.set_spatial_dims(x_dim="lon", y_dim="lat")
            # Use a clip box that covers the FULL grid extent so the
            # emitted cell count is deterministic (no clip-induced
            # exclusion of source pixels).
            geometry = gpd.GeoDataFrame(
                {
                    "district_id": ["test"],
                    "geometry": [shapely.geometry.box(76.0, 11.0, 79.0, 14.0)],
                },
                crs="EPSG:4326",
            )
            result = computation._extract_raster_grid(
                data,
                geometry,
                district_id="test",
                variable="precipitation",
                year=asset.year,
                month=asset.month,
            )
            assert result is not None
            for cell in result.cells:
                assert not np.isnan(cell.value)
                assert not np.isinf(cell.value)
                assert cell.min_lon < cell.max_lon
                assert cell.min_lat < cell.max_lat
            # 25 source pixels, 3 poisoned with NaN/±Inf → 22 survivors.
            assert len(result.cells) == 25 - 3
        finally:
            rds.close()
    finally:
        lease.release()


# ── Brief Scenario 2: 5 concurrent same-key ─────────────────────────────


@pytest.mark.asyncio
async def test_five_concurrent_same_key_coalesce(cache: RasterCache) -> None:
    storage = _FakeStorage()
    asset, body = _make_asset(body=b"e" * 2048)
    storage.seed(asset.storage_key, body)

    leases = await asyncio.gather(*[cache.acquire(asset, storage) for _ in range(5)])
    try:
        # Exactly one S3 download — all 5 callers coalesced.
        assert storage.download_calls.count(asset.storage_key) == 1
        # Exactly one fresh, four cache hits.
        misses = sum(1 for l in leases if not l.cache_hit)
        hits = sum(1 for l in leases if l.cache_hit)
        assert misses == 1
        assert hits == 4
        # All five leases point at the same on-disk cache file.
        paths = {l.path for l in leases}
        assert len(paths) == 1
    finally:
        for lease in leases:
            lease.release()


# ── Brief Scenario 3: different assets run in parallel ──────────────────


@pytest.mark.asyncio
async def test_different_assets_do_not_block(cache: RasterCache) -> None:
    storage = _FakeStorage()
    a, body_a = _make_asset(year=2025, month=7, body=b"f" * 1024)
    b, body_b = _make_asset(year=2025, month=8, body=b"g" * 1024)
    c, body_c = _make_asset(year=2025, month=9, body=b"h" * 1024)
    storage.seed(a.storage_key, body_a)
    storage.seed(b.storage_key, body_b)
    storage.seed(c.storage_key, body_c)

    leases = await asyncio.gather(
        cache.acquire(a, storage),
        cache.acquire(b, storage),
        cache.acquire(c, storage),
    )
    try:
        # Three downloads, three distinct cache files.
        assert storage.download_calls.count(a.storage_key) == 1
        assert storage.download_calls.count(b.storage_key) == 1
        assert storage.download_calls.count(c.storage_key) == 1
        assert {l.path.name for l in leases} == {"07.nc", "08.nc", "09.nc"}
    finally:
        for lease in leases:
            lease.release()


# ── Brief Scenario 4: cache wipe rebuilds from S3, never CDS ───────────


@pytest.mark.asyncio
async def test_cache_wipe_redownloads_from_s3(cache: RasterCache) -> None:
    storage = _FakeStorage()
    asset, body = _make_asset(body=b"i" * 2048)
    storage.seed(asset.storage_key, body)

    first = await cache.acquire(asset, storage)
    cache_path = first.path
    sidecar = _sidecar_path(cache_path)
    assert cache_path.exists()
    assert sidecar.exists()
    first.release()

    # Wipe both the cache file and its sidecar (simulates operator
    # deleting ``data/era5``).
    cache_path.unlink()
    sidecar.unlink()
    assert not cache_path.exists()
    assert not sidecar.exists()

    # The fake storage has no CDS hook; the next acquire must rebuild
    # from the fake's "S3" (download_objects dict), proving that the
    # cache wipe never escalates to CDS.
    second = await cache.acquire(asset, storage)
    second.release()

    assert second.cache_hit is False
    assert cache_path.exists()
    assert sidecar.exists()
    # Exactly one extra S3 download (total two for the same key).
    assert storage.download_calls.count(asset.storage_key) == 2


# ── Brief Scenario 5: stale/corrupt cache artifact recovers ────────────


@pytest.mark.asyncio
async def test_checksum_drift_redownloads_from_s3(cache: RasterCache) -> None:
    storage = _FakeStorage()
    asset, body = _make_asset(body=b"j" * 2048)
    storage.seed(asset.storage_key, body)

    first = await cache.acquire(asset, storage)
    cache_path = first.path
    sidecar = _sidecar_path(cache_path)
    first.release()

    # Corrupt the cache file in place. The sidecar still claims the
    # original (good) fingerprint, so a fast-path validate should
    # detect the drift and fall through to a redownload.
    cache_path.write_bytes(b"corrupted-blob")

    second = await cache.acquire(asset, storage)
    second.release()

    assert second.cache_hit is False
    # The corrupted file was replaced with the authoritative one.
    assert sha256_file(cache_path) == asset.checksum
    assert storage.download_calls.count(asset.storage_key) == 2


@pytest.mark.asyncio
async def test_size_drift_redownloads_from_s3(cache: RasterCache) -> None:
    """If the file size on disk does not match ``asset."""
    storage = _FakeStorage()
    asset, body = _make_asset(body=b"k" * 2048)
    storage.seed(asset.storage_key, body)

    first = await cache.acquire(asset, storage)
    cache_path = first.path
    first.release()

    # Truncate the file by half.
    with cache_path.open("rb+") as f:
        f.truncate(1024)

    second = await cache.acquire(asset, storage)
    second.release()

    assert second.cache_hit is False
    assert cache_path.stat().st_size == asset.file_size


# ── Brief Scenario 6: cleanup / bounded size ────────────────────────────


@pytest.mark.asyncio
async def test_eviction_sweep_frees_bytes(tmp_path: Path) -> None:
    """The lru sweep evicts oldest files first when over budget."""
    cache = RasterCache(cache_root=tmp_path / "cache", max_bytes=2 * 1024)
    storage = _FakeStorage()

    paths: list[Path] = []
    for m in (1, 2, 3):
        a, body = _make_asset(year=2025, month=m, body=b"l" * 1024)
        storage.seed(a.storage_key, body)
        lease = await cache.acquire(a, storage)
        paths.append(lease.path)
        lease.release()

    # Total cache is now ~3 KiB; budget is 2 KiB. The oldest file
    # (month 1) must be evicted; the most-recently-read file
    # (month 3) must be kept.
    _maybe_sweep_cache(
        cache.cache_root,
        max_bytes=2 * 1024,
        active_keys=set(),
    )

    assert not paths[0].exists()
    assert paths[2].exists()


# ── Partial-file cleanup on failure ─────────────────────────────────────


@pytest.mark.asyncio
async def test_partial_files_cleaned_on_corrupt_download(
    cache: RasterCache,
) -> None:
    """If s3 returns bytes whose sha256 does not match the db row,."""
    storage = _FakeStorage()
    asset, body = _make_asset(body=b"m" * 2048)
    storage.seed(asset.storage_key, body)

    # Wipe the cache up front so the second acquire must download.
    cp = cache.cache_path_for(asset)
    cp.unlink(missing_ok=True)
    sidecar = _sidecar_path(cp)
    sidecar.unlink(missing_ok=True)

    # Replace the S3 object with bytes that do NOT match the DB
    # checksum.
    storage.seed(asset.storage_key, b"different-bytes-!@#")

    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        await cache.acquire(asset, storage)

    # No cache file should have been published; the .partial sibling
    # must also be gone.
    assert not cp.exists()
    assert not list(cache.cache_root.rglob("*.partial"))


# ── Sidecar-driven fast validation direct test ─────────────────────────


def test_validate_fast_detects_missing_sidecar(tmp_path: Path) -> None:
    """If the cache file exists but its sidecar is missing, the."""
    f = tmp_path / "07.nc"
    f.write_bytes(b"x" * 100)
    assert _validate_fast(f, expected_sha=_sha(b"x" * 100), expected_size=100) is False


def test_validate_fast_detects_size_drift(tmp_path: Path) -> None:
    f = tmp_path / "07.nc"
    body = b"x" * 100
    f.write_bytes(body)
    _publish_with_sidecar(f, f, expected_sha=_sha(body))
    # Truncate the file — sidecar still claims 100 bytes.
    with f.open("rb+") as fh:
        fh.truncate(50)
    assert _validate_fast(f, expected_sha=_sha(body), expected_size=100) is False


def test_validate_fast_accepts_good_artifact(tmp_path: Path) -> None:
    f = tmp_path / "07.nc"
    body = b"good" * 1000
    f.write_bytes(body)
    _publish_with_sidecar(f, f, expected_sha=_sha(body))
    assert _validate_fast(f, expected_sha=_sha(body), expected_size=len(body)) is True


def test_validate_fast_detects_fingerprint_drift(tmp_path: Path) -> None:
    f = tmp_path / "07.nc"
    body = b"original" * 1000
    f.write_bytes(body)
    _publish_with_sidecar(f, f, expected_sha=_sha(body))
    # Corrupt the head bytes (first 64 KiB).
    with f.open("rb+") as fh:
        fh.write(b"X" * 100)
    assert _validate_fast(f, expected_sha=_sha(body), expected_size=len(body)) is False
    # The corrupted file should have been unlinked.
    assert not f.exists()
