"""Tests for the ingestion/era5 module — postgresql-as-source-of-truth era."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import uuid
import zipfile
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from types import SimpleNamespace
from typing import Sequence

import pytest
import xarray as xr

from core.config import Settings
from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort
from ingestion.era5.checksums import sha256_file
from ingestion.era5.downloader import CdsClient, DatasetHandle, Downloader
from ingestion.era5.file_service import FileService
from ingestion.era5.hashing import month_bundle_hash
from ingestion.era5.locks import LockRegistry
from ingestion.era5.splitter import (
    DEFAULT_ERA5_VARIABLES,
    DatasetSplitter,
    Era5Variable,
    SplitFile,
    VARIABLE_CATEGORY,
)
from ingestion.era5.timing import (
    EnsureSummary,
    PhaseTimer,
    build_ensure_summary,
    log_ensure_summary,
)


# ── Fakes ─────────────────────────────────────────────────────────────────


class FakeCdsClient:
    """Synchronous cds double that records every retrieve call."""

    def __init__(self, block_seconds: float = 0.0) -> None:
        self._lock = Lock()
        self.requests: list[tuple[str, dict[str, object], str]] = []
        self._block_seconds = block_seconds

    def retrieve(self, name: str, request: dict[str, object], target: str) -> object:
        with self._lock:
            self.requests.append((name, request, target))
        if self._block_seconds > 0:
            import time

            time.sleep(self._block_seconds)
        Path(target).write_bytes(b"fake-netcdf-bytes")
        return None


class FakeStoragePort(StoragePort):
    """In-memory storageport double that round-trips uploads and downloads."""

    def __init__(self) -> None:
        self.uploads: list[tuple[str, int]] = []
        self.downloads: list[tuple[str, int]] = []
        self._store: dict[str, bytes] = {}
        self._lock = Lock()

    def upload(self, key: str, data) -> None:
        if isinstance(data, Path):
            payload = data.read_bytes()
        elif isinstance(data, bytes):
            payload = data
        else:
            payload = data.read()
        with self._lock:
            self._store[key] = payload
            self.uploads.append((key, len(payload)))

    def download(self, key: str, target) -> None:
        with self._lock:
            payload = self._store.get(key)
        if payload is None:
            raise FileNotFoundError(f"fake storage has no object for key {key!r}")
        if isinstance(target, Path):
            target.write_bytes(payload)
        else:
            target.write(payload)
        with self._lock:
            self.downloads.append((key, len(payload)))

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._store

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return ""

    def list(self, prefix: str = "") -> Sequence[str]:
        with self._lock:
            return [k for k in self._store if k.startswith(prefix)]


class FakeDatasetRepository(DatasetRepository):
    """In-memory datasetrepository double that mirrors the production."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, int, int], ClimateAsset] = {}
        self._lock = Lock()
        self.saved: list[ClimateAsset] = []
        self._save_event: asyncio.Event | None = None

    def attach_save_event(self, event: asyncio.Event) -> None:
        self._save_event = event

    async def save(self, asset: ClimateAsset) -> ClimateAsset:
        with self._lock:
            if asset.id is None:
                saved = ClimateAsset(
                    id=str(uuid.uuid4()),
                    provider=asset.provider,
                    variable=asset.variable,
                    year=asset.year,
                    month=asset.month,
                    storage_key=asset.storage_key,
                    checksum=asset.checksum,
                    file_size=asset.file_size,
                    status=asset.status,
                    created_at=asset.created_at,
                    updated_at=asset.updated_at,
                )
            else:
                saved = asset
            self._rows[
                (saved.provider, saved.variable, saved.year, saved.month)
            ] = saved
            self.saved.append(saved)
        if self._save_event is not None:
            self._save_event.set()
        return saved

    async def get_by_id(self, asset_id: str) -> ClimateAsset | None:
        for asset in self._rows.values():
            if asset.id == asset_id:
                return asset
        return None

    async def get_by_period(
        self, year: int, month: int, provider: str, variable: str
    ) -> ClimateAsset | None:
        return self._rows.get((provider, variable, year, month))

    async def list(self) -> Sequence[ClimateAsset]:
        return list(self._rows.values())

    async def list_by_period_range(self, *args, **kwargs) -> Sequence[ClimateAsset]:
        return list(self._rows.values())

    async def get_available_range(self, *args, **kwargs):
        return None

    async def delete(self, asset_id: str) -> None:
        with self._lock:
            for key, asset in list(self._rows.items()):
                if asset.id == asset_id:
                    del self._rows[key]
                    return

    async def exists(self, provider: str, variable: str, year: int, month: int) -> bool:
        return (provider, variable, year, month) in self._rows


class FakeSplitter:
    """Splitter double that materialises predetermined split files without."""

    def __init__(self, splits: list[SplitFile] | None = None) -> None:
        self.splits = splits or []
        self.calls: list[tuple[Path, int, int, Path]] = []
        self.sources_were_zips: list[bool] = []

    def split(self, source: Path, year: int, month: int, temp_dir: Path) -> list[SplitFile]:
        self.calls.append((source, year, month, temp_dir))
        self.sources_were_zips.append(zipfile.is_zipfile(source))
        for sf in self.splits:
            sf.path.parent.mkdir(parents=True, exist_ok=True)
            sf.path.write_bytes(b"fake-split-bytes")
        return list(self.splits)


class FakeBlockingCdsClient:
    """Cds double that blocks on a threading event until the test releases it."""

    def __init__(self, gate: "threading.Event | None" = None) -> None:
        from threading import Event as ThreadingEvent

        self._lock = Lock()
        self.requests: list[tuple[str, dict[str, object], str]] = []
        self._gate: ThreadingEvent | None = gate

    def retrieve(self, name: str, request: dict[str, object], target: str) -> object:
        with self._lock:
            self.requests.append((name, request, target))
        if self._gate is not None:
            self._gate.wait()
        Path(target).write_bytes(b"fake-netcdf-bytes")
        return None


# ── Settings / build helpers ──────────────────────────────────────────────


def _make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
        app_name="test",
        version="0.0.0",
        environment="development",
        log_level="INFO",
        aws_region="us-east-1",
        aws_access_key_id="AKIA-TEST",
        aws_secret_access_key="test-secret",
        s3_bucket_name="test-bucket",
        s3_endpoint_url="https://s3.test",
        database_url="postgresql://test:test@localhost/test",
        era5_storage_root=tmp_path / "storage",
        era5_logs_dir=tmp_path / "logs",
        era5_s3_prefix="era5-land-test",
        era5_dataset="reanalysis-era5-land-monthly-means",
        era5_max_months=480,
        era5_retry_attempts=3,
        era5_retry_base_seconds=0.0,
        era5_bootstrap_months=24,
        # Explicit CDS defaults so backend/.env cannot leak real
        # credentials into the test's notion of "no creds".
        cdsapi_url=None,
        cdsapi_key=None,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _build(
    tmp_path: Path,
    *,
    cds_client: CdsClient | None = None,
    splitter: object | None = None,
    storage_port: object | None = None,
    locks: LockRegistry | None = None,
    **settings_overrides,
) -> tuple[Downloader, Settings, FakeCdsClient, FileService]:
    settings = _make_settings(tmp_path, **settings_overrides)
    storage_root = settings.era5_storage_root_resolved()
    temp_dir = storage_root / "tmp"
    files = FileService(storage_root=storage_root, temp_dir=temp_dir)
    cds = cds_client or FakeCdsClient()
    downloader = Downloader(
        settings=settings,
        files=files,
        splitter=splitter,
        storage_port=storage_port,
        locks=locks or LockRegistry(),
        cds_client=cds,
    )
    return downloader, settings, cds, files  # type: ignore[return-value]


def _make_split_files(
    storage_root: Path,
    year: int,
    month: int,
    *,
    only: str | None = None,
) -> list[SplitFile]:
    """Build a deterministic per-variable splitfile triple (or a single."""
    categories: list[tuple[str, str]] = [
        ("tp", "precipitation"),
        ("swvl1", "soil_moisture"),
        ("sro", "surface_runoff"),
    ]
    if only is not None:
        categories = [(v, c) for v, c in categories if c == only]
    return [
        SplitFile(
            variable=var,
            category=category,
            path=storage_root / "tmp" / f"{category}_{year:04d}_{month:02d}.nc",
            file_size=64,
            checksum="placeholder",
        )
        for var, category in categories
    ]


# ── FileService / per-variable cache tests ────────────────────────────────


def test_cache_path_for_layout(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()
    files = FileService(storage_root=storage_root, temp_dir=storage_root / "tmp")

    path = files.cache_path_for("era5-land", "precipitation", 2024, 5)

    assert path == storage_root / "cache" / "era5-land" / "precipitation" / "2024" / "05.nc"


def test_cache_path_for_rejects_traversal(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()
    files = FileService(storage_root=storage_root, temp_dir=storage_root / "tmp")

    with pytest.raises(ValueError):
        files.cache_path_for("../etc", "precipitation", 2024, 5)
    with pytest.raises(ValueError):
        files.cache_path_for("era5-land", "soil_moisture/../../etc", 2024, 5)
    with pytest.raises(ValueError):
        files.cache_path_for("", "precipitation", 2024, 5)


def test_ensure_cache_dir_creates_only_parent(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()
    files = FileService(storage_root=storage_root, temp_dir=storage_root / "tmp")

    parent = files.ensure_cache_dir("era5-land", "surface_runoff", 2024, 7)

    assert parent.is_dir()
    # No file should be created by ensure_cache_dir itself.
    cache_path = files.cache_path_for("era5-land", "surface_runoff", 2024, 7)
    assert not cache_path.exists()


# ── LockRegistry tests ────────────────────────────────────────────────────


def test_lock_registry_no_manifest_lock() -> None:
    """``manifest_lock`` is gone — the registry only exposes the per-key."""
    registry = LockRegistry()
    assert not hasattr(registry, "manifest_lock")
    assert hasattr(registry, "download_lock")
    assert hasattr(registry, "queue_lock")


def test_lock_registry_download_lock_is_per_key() -> None:
    registry = LockRegistry()
    lock_a = registry.download_lock("a")
    lock_b = registry.download_lock("b")
    lock_a2 = registry.download_lock("a")

    # Same key -> same lock.
    assert lock_a is lock_a2
    # Different keys -> different locks.
    assert lock_a is not lock_b


# ── Timing helper tests ───────────────────────────────────────────────────


def test_phase_timer_records_per_phase_elapsed() -> None:
    timer = PhaseTimer()
    timer.start_total()
    with timer.phase("metadata_lookup"):
        pass
    with timer.phase("s3_download"):
        pass
    with timer.phase("metadata_lookup"):  # re-entrant: accumulates
        pass

    assert timer.elapsed_ms["metadata_lookup"] >= 0
    assert timer.elapsed_ms["s3_download"] >= 0
    # Two metadata_lookup phases sum to roughly the same magnitude as one
    # + a tiny epsilon; the test only requires the dict to reflect the
    # accumulated value (the inner elapsed is always >= 0).
    assert timer.elapsed_ms["metadata_lookup"] >= timer.elapsed_ms["s3_download"] - 1


def test_phase_timer_phases_default_to_zero_in_summary() -> None:
    timer = PhaseTimer()
    timer.start_total()

    summary = build_ensure_summary(
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=5,
        phases=timer.elapsed_ms,
        cache_hit="n/a",
        source="db",
        total_ms=timer.total_ms(),
    )

    assert summary.metadata_lookup_ms == 0.0
    assert summary.s3_download_ms == 0.0
    assert summary.era5_download_ms == 0.0
    assert summary.s3_upload_ms == 0.0


def test_log_ensure_summary_emits_record(tmp_path: Path, caplog) -> None:
    logger = logging.getLogger("test.ingestion.timing")
    with caplog.at_level(logging.INFO, logger="test.ingestion.timing"):
        summary = log_ensure_summary(
            logger,
            provider="era5-land",
            variable="precipitation",
            year=2024,
            month=5,
            phases={"metadata_lookup": 1.5, "s3_download": 200.0},
            cache_hit=True,
            source="db",
            total_ms=210.0,
        )

    assert isinstance(summary, EnsureSummary)
    assert summary.provider == "era5-land"
    assert summary.local_cache_hit is True
    assert summary.source == "db"
    assert summary.total_ms == 210.0

    matching = [r for r in caplog.records if r.name == "test.ingestion.timing"]
    assert matching, "expected at least one INFO record"
    record = matching[0]
    assert getattr(record, "event") == "dataset.ensure"
    assert getattr(record, "variable") == "precipitation"
    assert getattr(record, "metadata_lookup_ms") == 1.5


# ── Downloader.ensure_dataset tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_dataset_cache_hit_skips_cds_and_s3(tmp_path: Path) -> None:
    storage_port = FakeStoragePort()
    repository = FakeDatasetRepository()
    downloader, settings, cds, files = _build(
        tmp_path, splitter=FakeSplitter(), storage_port=storage_port
    )

    cache_path = files.cache_path_for("era5-land", "precipitation", 2024, 5)
    files.ensure_cache_dir("era5-land", "precipitation", 2024, 5)
    cache_path.write_bytes(b"already-on-disk")
    expected_checksum = sha256_file(cache_path)
    asset = ClimateAsset(
        id="asset-1",
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=5,
        storage_key="era5-land-test/precipitation/2024/05.nc",
        checksum=expected_checksum,
        file_size=cache_path.stat().st_size,
        status=ClimateAssetStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await repository.save(asset)

    handle = await downloader.ensure_dataset(
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=5,
        repository=repository,
    )

    assert handle.cache_hit is True
    assert handle.local_path == cache_path
    assert handle.checksum == expected_checksum
    assert len(cds.requests) == 0
    assert len(storage_port.downloads) == 0
    assert len(storage_port.uploads) == 0


@pytest.mark.asyncio
async def test_ensure_dataset_wiped_cache_redownloads_from_s3(tmp_path: Path) -> None:
    storage_port = FakeStoragePort()
    repository = FakeDatasetRepository()
    downloader, settings, cds, files = _build(
        tmp_path, splitter=FakeSplitter(), storage_port=storage_port
    )

    # Seed S3 + DB as if a prior ingestion had succeeded.
    cache_path = files.cache_path_for("era5-land", "precipitation", 2024, 6)
    files.ensure_cache_dir("era5-land", "precipitation", 2024, 6)
    cache_path.write_bytes(b"in-s3-only")
    expected_checksum = sha256_file(cache_path)
    s3_key = "era5-land-test/precipitation/2024/06.nc"
    # Round-trip through FakeStoragePort so the fake has the bytes.
    storage_port.upload(s3_key, cache_path)
    storage_port.uploads.clear()

    asset = ClimateAsset(
        id="asset-2",
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=6,
        storage_key=s3_key,
        checksum=expected_checksum,
        file_size=cache_path.stat().st_size,
        status=ClimateAssetStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await repository.save(asset)

    # Wipe the local cache.
    cache_path.unlink()
    assert not cache_path.exists()

    handle = await downloader.ensure_dataset(
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=6,
        repository=repository,
    )

    assert handle.cache_hit is False
    assert handle.local_path == cache_path
    assert handle.checksum == expected_checksum
    # CDS must NOT have been called — the DB row is authoritative.
    assert len(cds.requests) == 0
    # S3 download ran exactly once and re-populated the cache file.
    assert len(storage_port.downloads) == 1
    assert storage_port.downloads[0][0] == s3_key
    assert cache_path.exists()


@pytest.mark.asyncio
async def test_ensure_dataset_checksum_drift_redownloads_from_s3(tmp_path: Path) -> None:
    """A cache file with a checksum that does not match the db row."""
    storage_port = FakeStoragePort()
    repository = FakeDatasetRepository()
    downloader, _, _, files = _build(
        tmp_path, splitter=FakeSplitter(), storage_port=storage_port
    )

    cache_path = files.cache_path_for("era5-land", "precipitation", 2024, 7)
    files.ensure_cache_dir("era5-land", "precipitation", 2024, 7)
    cache_path.write_bytes(b"corrupted-locally")
    s3_key = "era5-land-test/precipitation/2024/07.nc"
    storage_port.upload(s3_key, b"authoritative-from-s3")

    asset = ClimateAsset(
        id="asset-3",
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=7,
        storage_key=s3_key,
        checksum=sha256_file_from_bytes(b"authoritative-from-s3"),
        file_size=len(b"authoritative-from-s3"),
        status=ClimateAssetStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await repository.save(asset)

    handle = await downloader.ensure_dataset(
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=7,
        repository=repository,
    )

    assert handle.cache_hit is False
    assert handle.checksum == asset.checksum
    assert cache_path.read_bytes() == b"authoritative-from-s3"
    assert len(storage_port.downloads) == 1


@pytest.mark.asyncio
async def test_ensure_dataset_cache_miss_fetches_from_cds(tmp_path: Path) -> None:
    storage_port = FakeStoragePort()
    repository = FakeDatasetRepository()
    settings = _make_settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()
    splits = _make_split_files(storage_root, 2024, 5, only="precipitation")
    splitter = FakeSplitter(splits=splits)
    downloader, _, cds, files = _build(
        tmp_path, splitter=splitter, storage_port=storage_port
    )

    handle = await downloader.ensure_dataset(
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=5,
        repository=repository,
    )

    assert handle.cache_hit is False
    assert handle.storage_key == "era5-land-test/precipitation/2024/05.nc"
    assert handle.local_path.exists()
    assert handle.local_path.read_bytes() == b"fake-split-bytes"
    assert handle.file_size == len(b"fake-split-bytes")

    # CDS called exactly once for the requested variable only.
    assert len(cds.requests) == 1
    request = cds.requests[0][1]
    assert request["variable"] == ["total_precipitation"]
    assert request["year"] == "2024"
    assert request["month"] == "05"

    # One S3 upload; no S3 download (we went straight CDS -> cache -> S3).
    assert len(storage_port.uploads) == 1
    assert storage_port.uploads[0][0] == handle.storage_key
    assert len(storage_port.downloads) == 0

    # ClimateAsset row inserted with the right (provider, variable, year, month).
    assert len(repository.saved) == 1
    saved = repository.saved[0]
    assert saved.provider == "era5-land"
    assert saved.variable == "precipitation"
    assert saved.year == 2024
    assert saved.month == 5
    assert saved.status == ClimateAssetStatus.COMPLETED
    assert saved.checksum == handle.checksum

    # The fake-split temp file is cleaned up; only the cache file remains.
    assert not (storage_root / "tmp" / "precipitation_2024_05.nc").exists()
    assert not (storage_root / "2024" / "hydrology_2024_05.nc").exists()


@pytest.mark.asyncio
async def test_ensure_dataset_concurrent_same_variable_coalesces(tmp_path: Path) -> None:
    """Four concurrent callers for the same variable on the same month."""
    from threading import Event as ThreadingEvent

    gate = ThreadingEvent()
    cds = FakeBlockingCdsClient(gate=gate)
    storage_port = FakeStoragePort()
    repository = FakeDatasetRepository()
    settings = _make_settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()
    splits = _make_split_files(storage_root, 2024, 5, only="precipitation")
    downloader, _, _, _ = _build(
        tmp_path,
        cds_client=cds,
        splitter=FakeSplitter(splits=splits),
        storage_port=storage_port,
    )

    async def runner() -> DatasetHandle:
        return await downloader.ensure_dataset(
            provider="era5-land",
            variable="precipitation",
            year=2024,
            month=5,
            repository=repository,
        )

    # Start four concurrent callers. Caller A enters CDS first and parks
    # on the gate; callers B, C, D queue on the per-variable lock.
    tasks = [asyncio.create_task(runner()) for _ in range(4)]
    # Wait until caller A has reached ``_retrieve`` (it parks there).
    # Poll with a short sleep to avoid hard-coding a magic sleep duration.
    for _ in range(200):
        if cds.requests:
            break
        await asyncio.sleep(0.01)
    assert cds.requests, "caller A never reached CDS"
    # Release caller A's CDS gate so the upload + register can finish.
    gate.set()

    handles = await asyncio.gather(*tasks)

    assert len(cds.requests) == 1
    assert len(repository.saved) == 1
    # All four callers received a usable handle.
    assert all(isinstance(h, DatasetHandle) for h in handles)
    # Caller A was the "first writer" and saw cache_hit=False; the
    # remaining three saw the row appear and either served from cache
    # or hit S3 (but never CDS again).
    cache_hit_counts = {h.cache_hit for h in handles}
    assert False in cache_hit_counts


@pytest.mark.asyncio
async def test_ensure_dataset_concurrent_different_variables_run_in_parallel(
    tmp_path: Path,
) -> None:
    """Concurrent callers for different variables on the same month."""
    cds = FakeCdsClient()
    storage_port = FakeStoragePort()
    repository = FakeDatasetRepository()
    settings = _make_settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()

    # Two splits so the splitter returns both requested variables when
    # we ask for both back-to-back. The downloader calls the splitter
    # once per ``ensure_dataset`` invocation, so each call needs only
    # the variable it asked for.
    splits_precip = _make_split_files(storage_root, 2024, 5, only="precipitation")
    splits_soil = _make_split_files(storage_root, 2024, 5, only="soil_moisture")
    downloader_p, _, _, _ = _build(
        tmp_path,
        cds_client=cds,
        splitter=FakeSplitter(splits=splits_precip),
        storage_port=storage_port,
    )
    downloader_s, _, _, _ = _build(
        tmp_path,
        cds_client=cds,
        splitter=FakeSplitter(splits=splits_soil),
        storage_port=storage_port,
    )

    async def runner(d: Downloader, variable: str) -> DatasetHandle:
        return await d.ensure_dataset(
            provider="era5-land",
            variable=variable,
            year=2024,
            month=5,
            repository=repository,
        )

    handle_p, handle_s = await asyncio.gather(
        runner(downloader_p, "precipitation"),
        runner(downloader_s, "soil_moisture"),
    )

    # Two CDS requests — one per variable.
    cds_variables = {req[1]["variable"][0] for req in cds.requests}
    assert cds_variables == {"total_precipitation", "volumetric_soil_water_layer_1"}
    # Two DB rows, one per variable.
    saved_variables = {a.variable for a in repository.saved}
    assert saved_variables == {"precipitation", "soil_moisture"}
    # Two S3 uploads.
    assert len(storage_port.uploads) == 2
    assert handle_p.storage_key == "era5-land-test/precipitation/2024/05.nc"
    assert handle_s.storage_key == "era5-land-test/soil_moisture/2024/05.nc"


@pytest.mark.asyncio
async def test_ensure_dataset_normalizes_cds_zip(tmp_path: Path) -> None:
    """When cds returns a zip-wrapped netcdf, ``_ensure_normalized``."""
    class ZipCdsClient:
        def retrieve(self, name: str, request: dict[str, object], target: str) -> object:
            with zipfile.ZipFile(target, "w") as zf:
                zf.writestr("data.nc", b"inner-netcdf-bytes")
            return None

    settings = _make_settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()
    splits = _make_split_files(storage_root, 2024, 7, only="precipitation")
    splitter = FakeSplitter(splits=splits)
    storage_port = FakeStoragePort()
    repository = FakeDatasetRepository()
    downloader, _, _, files = _build(
        tmp_path,
        cds_client=ZipCdsClient(),
        splitter=splitter,
        storage_port=storage_port,
    )

    await downloader.ensure_dataset(
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=7,
        repository=repository,
    )

    # The splitter was called exactly once and was given the bundle path,
    # not the temp_bundle or the original ZIP. The bundle_path is
    # deleted during cleanup, so we use the FakeSplitter's recorded
    # ZIP-status flag instead of asserting on disk state post-call.
    assert len(splitter.calls) == 1
    source_passed_to_splitter = splitter.calls[0][0]
    bundle_path = storage_root / "2024" / "hydrology_2024_07.nc"
    assert source_passed_to_splitter == bundle_path
    assert splitter.sources_were_zips == [False], (
        "splitter must not receive a ZIP; _ensure_normalized should "
        "extract the inner .nc before split is invoked"
    )


# ── Era5Variable tests (preserved) ────────────────────────────────────────


def test_era5_variable_accepts_multiple_aliases() -> None:
    v = Era5Variable(name="surface_runoff", aliases=("ro", "sro"))
    assert v.aliases == ("ro", "sro")
    assert v.alias == "ro"


def test_era5_variable_dedupes_and_validates_aliases() -> None:
    v = Era5Variable(name="x", aliases=("a", "b", "a"))
    assert v.aliases == ("a", "b")

    v2 = Era5Variable(name="x", aliases=["a", "b"])  # type: ignore[arg-type]
    assert v2.aliases == ("a", "b")

    with pytest.raises(ValueError):
        Era5Variable(name="x", aliases=())

    with pytest.raises(TypeError):
        Era5Variable(name="x", aliases=(1, 2))  # type: ignore[arg-type]


def test_default_era5_variables_surface_runoff_has_dual_aliases() -> None:
    by_name = {v.name: v for v in DEFAULT_ERA5_VARIABLES}
    assert set(by_name["surface_runoff"].aliases) == {"ro", "sro"}


# ── DatasetSplitter tests (preserved) ─────────────────────────────────────


def _write_synthetic_bundle(path: Path, data_vars: dict[str, list[float]]) -> None:
    ds = xr.Dataset({k: (("time",), list(v)) for k, v in data_vars.items()})
    ds.to_netcdf(str(path), engine="netcdf4")


def test_splitter_handles_sro_alias(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.nc"
    _write_synthetic_bundle(
        bundle,
        {"tp": [1.0], "swvl1": [0.2], "sro": [0.05]},
    )
    splitter = DatasetSplitter()
    result = splitter.split(bundle, 2024, 5, tmp_path)

    assert len(result) == 3
    by_var = {sf.variable: sf for sf in result}
    assert set(by_var) == {"tp", "swvl1", "sro"}
    assert by_var["sro"].category == "surface_runoff"
    assert (tmp_path / "precipitation_2024_05.nc").exists()
    assert (tmp_path / "soil_moisture_2024_05.nc").exists()
    assert (tmp_path / "surface_runoff_2024_05.nc").exists()


def test_splitter_still_handles_legacy_ro_alias(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.nc"
    _write_synthetic_bundle(
        bundle,
        {"tp": [1.0], "swvl1": [0.2], "ro": [0.05]},
    )
    splitter = DatasetSplitter()
    result = splitter.split(bundle, 2024, 5, tmp_path)

    assert len(result) == 3
    assert {sf.variable for sf in result} == {"tp", "swvl1", "ro"}
    assert all(sf.category == "surface_runoff" for sf in result if sf.variable == "ro")


def test_splitter_warns_on_unknown_variable(tmp_path: Path, caplog) -> None:
    import logging

    bundle = tmp_path / "bundle.nc"
    _write_synthetic_bundle(
        bundle,
        {"tp": [1.0], "swvl1": [0.2], "rogue": [99.0]},
    )
    splitter = DatasetSplitter()
    with caplog.at_level(logging.WARNING, logger="ingestion.era5.splitter"):
        result = splitter.split(bundle, 2024, 5, tmp_path)

    assert len(result) == 2
    assert {sf.variable for sf in result} == {"tp", "swvl1"}
    rogue_records = [r for r in caplog.records if "rogue" in r.getMessage()]
    assert rogue_records


# ── Settings tests (preserved) ─────────────────────────────────────────────


def test_settings_cds_credentials_validation(tmp_path: Path) -> None:
    no_creds = _make_settings(tmp_path)
    assert no_creds.cds_credentials_configured() is False

    placeholder = _make_settings(tmp_path, cdsapi_key="replace-with-your-cds-api-key")
    assert placeholder.cds_credentials_configured() is False

    good = _make_settings(tmp_path, cdsapi_url="https://cds.test/api", cdsapi_key="real-key")
    assert good.cds_credentials_configured() is True


def test_settings_era5_paths_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ERA5_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("ERA5_LOGS_DIR", raising=False)
    settings = _make_settings(tmp_path)
    assert settings.era5_storage_root_resolved() == tmp_path / "storage"
    assert settings.era5_logs_dir_resolved() == tmp_path / "logs"
    assert settings.raster_cache_root_resolved() == tmp_path / "storage" / "cache"


# ── CLI status tests ──────────────────────────────────────────────────────


def test_cli_status_reads_postgres(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """``cmd_status`` must surface every row in ``climate_assets`` and."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
    from infrastructure.db.climate_asset_model import Base, ClimateAssetModel

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    SessionMaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Seed three rows: two months of precipitation, one of soil_moisture.
        # We persist the ORM ``ClimateAssetModel`` directly because the CLI
        # queries the table (not the domain entity).
        now = datetime.now(timezone.utc)
        async with SessionMaker() as session:
            for (variable, year, month) in [
                ("precipitation", 2024, 5),
                ("precipitation", 2024, 6),
                ("soil_moisture", 2024, 5),
            ]:
                model = ClimateAssetModel(
                    id=str(uuid.uuid4()),
                    provider="era5-land",
                    variable=variable,
                    year=year,
                    month=month,
                    storage_key=f"era5-land/{variable}/{year:04d}/{month:02d}.nc",
                    checksum="abc",
                    file_size=123,
                    status=ClimateAssetStatus.COMPLETED,
                    created_at=now,
                    updated_at=now,
                )
                session.add(model)
            await session.commit()

    asyncio.run(setup())

    # Patch the production async_session_maker to use our isolated engine.
    from ingestion.era5 import cli as cli_module

    monkeypatch.setattr(cli_module, "async_session_maker", SessionMaker)

    args = SimpleNamespace(variable=None)
    rc = cli_module.cmd_status(args)
    captured = capsys.readouterr().out

    assert rc == 0
    assert "PostgreSQL = source of truth" in captured
    assert "precipitation" in captured
    assert "soil_moisture" in captured
    assert "2024-05" in captured and "2024-06" in captured
    # The status command must NOT mention the old manifest filename.
    assert "manifest.json" not in captured

    asyncio.run(engine.dispose())


def test_cli_parser_accepts_variable_flag() -> None:
    from ingestion.era5.cli import build_parser

    parser = build_parser()

    args = parser.parse_args(["download", "2024", "5", "--variable", "precipitation"])
    assert args.variable == "precipitation"

    args = parser.parse_args(["bootstrap", "3"])
    assert args.variable is None
    assert args.months == 3

    args = parser.parse_args(["backfill", "2024"])
    assert args.variable is None

    args = parser.parse_args(["status"])
    assert args.variable is None

    args = parser.parse_args(["status", "--variable", "soil_moisture"])
    assert args.variable == "soil_moisture"


def test_cli_resolve_variables_validates_unknown(tmp_path: Path) -> None:
    from ingestion.era5.cli import _resolve_variables

    assert _resolve_variables(None) == ["precipitation", "soil_moisture", "surface_runoff"]
    assert _resolve_variables("precipitation") == ["precipitation"]
    with pytest.raises(SystemExit):
        _resolve_variables("bogus_variable")


# ── Module-level invariant tests ─────────────────────────────────────────


def test_module_no_longer_exports_removed_symbols() -> None:
    """``manifestmanager``, ``downloadresult``, and the bundle-level."""
    import ingestion.era5 as era5_pkg
    from ingestion.era5 import cli

    # __init__ no longer surfaces removed names.
    assert "ManifestManager" not in era5_pkg.__all__
    assert "DownloadResult" not in era5_pkg.__all__
    # Downloader itself no longer carries the removed methods.
    downloader_attrs = set(dir(Downloader))
    assert "ensure_downloaded" not in downloader_attrs
    assert "publish" not in downloader_attrs
    assert "ensure_dataset" in downloader_attrs
    # The CLI module no longer references ManifestManager.
    assert not hasattr(cli, "ManifestManager")
    # No manifest imports remain in the package.
    import ingestion.era5.cli as cli_mod

    src = Path(cli_mod.__file__).read_text(encoding="utf-8")
    assert "manifest_manager" not in src
    assert "manifest_lock" not in src


# ── Helpers ───────────────────────────────────────────────────────────────


def sha256_file_from_bytes(blob: bytes) -> str:
    """Compute the sha256 of a byte string without writing to disk."""
    import hashlib

    return hashlib.sha256(blob).hexdigest()
