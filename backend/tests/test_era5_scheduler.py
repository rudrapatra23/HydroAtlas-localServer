from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from ingestion.era5.scheduler import (
    Era5SyncService,
    SyncOutcome,
    history_months,
    next_daily_run,
    run_scheduler_forever,
)
from tests.test_ingestion_era5 import FakeDatasetRepository, _make_settings


class FakeStorage:
    def __init__(self, existing: set[str] | None = None) -> None:
        self._existing = existing or set()
        self.exists_calls: list[str] = []

    def exists(self, key: str) -> bool:
        self.exists_calls.append(key)
        return key in self._existing

    def add(self, key: str) -> None:
        self._existing.add(key)


class RepoContext:
    def __init__(self, repo: FakeDatasetRepository) -> None:
        self._repo = repo

    async def __aenter__(self) -> FakeDatasetRepository:
        return self._repo

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@dataclass
class FakeHandle:
    local_path: Path
    storage_key: str
    checksum: str
    file_size: int
    cache_hit: bool
    timings_ms: dict[str, float]


class FakeDownloader:
    def __init__(self, storage: FakeStorage | None = None) -> None:
        self.ensure_calls: list[tuple[str, int, int]] = []
        self.repair_calls: list[tuple[str, int, int]] = []
        self.fail_ensure_for: dict[tuple[str, int, int], int] = {}
        self.storage = storage

    async def ensure_dataset(
        self,
        *,
        provider: str,
        variable: str,
        year: int,
        month: int,
        repository: FakeDatasetRepository,
    ) -> FakeHandle:
        self.ensure_calls.append((variable, year, month))
        key = (variable, year, month)
        remaining_failures = self.fail_ensure_for.get(key, 0)
        if remaining_failures > 0:
            self.fail_ensure_for[key] = remaining_failures - 1
            raise RuntimeError(f"CDS unavailable for {variable}/{year:04d}-{month:02d}")

        now = datetime.now(timezone.utc)
        asset = ClimateAsset(
            id=None,
            provider=provider,
            variable=variable,
            year=year,
            month=month,
            storage_key=f"era5-land/{variable}/{year:04d}/{month:02d}.nc",
            checksum=f"{variable}-{year:04d}-{month:02d}",
            file_size=123,
            status=ClimateAssetStatus.COMPLETED,
            created_at=now,
            updated_at=now,
        )
        saved = await repository.save(asset)
        if self.storage is not None:
            self.storage.add(saved.storage_key)
        return FakeHandle(
            local_path=Path(saved.storage_key.replace("/", "_")),
            storage_key=saved.storage_key,
            checksum=saved.checksum,
            file_size=saved.file_size,
            cache_hit=False,
            timings_ms={},
        )

    async def repair_registered_asset(
        self,
        *,
        asset: ClimateAsset,
        repository: FakeDatasetRepository,
    ) -> FakeHandle:
        self.repair_calls.append((asset.variable, asset.year, asset.month))
        if self.storage is not None:
            self.storage.add(asset.storage_key)
        return FakeHandle(
            local_path=Path(asset.storage_key.replace("/", "_")),
            storage_key=asset.storage_key,
            checksum=asset.checksum,
            file_size=asset.file_size,
            cache_hit=False,
            timings_ms={},
        )


def _asset(variable: str, year: int, month: int) -> ClimateAsset:
    now = datetime.now(timezone.utc)
    return ClimateAsset(
        id=f"{variable}-{year:04d}-{month:02d}",
        provider="era5-land",
        variable=variable,
        year=year,
        month=month,
        storage_key=f"era5-land/{variable}/{year:04d}/{month:02d}.nc",
        checksum=f"{variable}-{year:04d}-{month:02d}",
        file_size=123,
        status=ClimateAssetStatus.COMPLETED,
        created_at=now,
        updated_at=now,
    )


def _service(
    tmp_path: Path,
    *,
    repo: FakeDatasetRepository | None = None,
    downloader: FakeDownloader | None = None,
    storage: FakeStorage | None = None,
    categories: list[str] | None = None,
    history_years: int = 10,
) -> Era5SyncService:
    repository = repo or FakeDatasetRepository()
    active_storage = storage or FakeStorage()
    active_downloader = downloader or FakeDownloader(active_storage)
    if downloader is not None and getattr(active_downloader, "storage", None) is None:
        active_downloader.storage = active_storage
    settings = _make_settings(
        tmp_path,
        era5_history_years=history_years,
        era5_sync_concurrency=2,
        era5_scheduler_timezone="UTC",
    )
    return Era5SyncService(
        settings=settings,
        downloader=active_downloader,  # type: ignore[arg-type]
        storage=active_storage,  # type: ignore[arg-type]
        categories=categories or ["precipitation"],
        concurrency=2,
        repository_factory=lambda: RepoContext(repository),  # type: ignore[arg-type]
    )


def test_history_months_returns_dynamic_10_year_window() -> None:
    months = history_months(as_of=date(2026, 7, 9), history_years=10)

    assert len(months) == 120
    assert months[0] == (2016, 7)
    assert months[-1] == (2026, 6)


def test_history_months_excludes_current_month_and_handles_january_rollover() -> None:
    months = history_months(as_of=date(2026, 1, 15), history_years=10)

    assert len(months) == 120
    assert months[0] == (2016, 1)
    assert months[-1] == (2025, 12)
    assert (2026, 1) not in months


@pytest.mark.asyncio
async def test_sync_once_repairs_stale_db_row_when_s3_object_is_missing(tmp_path: Path) -> None:
    repo = FakeDatasetRepository()
    downloader = FakeDownloader()
    await repo.save(_asset("precipitation", 2026, 6))
    service = _service(
        tmp_path,
        repo=repo,
        downloader=downloader,
        storage=FakeStorage(existing=set()),
        categories=["precipitation"],
        history_years=1,
    )

    results = await service.sync_periods([(2026, 6)])

    assert results == [SyncOutcome("precipitation", 2026, 6, "repaired", "")]
    assert downloader.repair_calls == [("precipitation", 2026, 6)]
    assert downloader.ensure_calls == []


@pytest.mark.asyncio
async def test_sync_once_skips_completed_month_when_s3_object_exists(tmp_path: Path) -> None:
    repo = FakeDatasetRepository()
    downloader = FakeDownloader()
    asset = _asset("precipitation", 2026, 6)
    await repo.save(asset)
    service = _service(
        tmp_path,
        repo=repo,
        downloader=downloader,
        storage=FakeStorage(existing={asset.storage_key}),
        categories=["precipitation"],
        history_years=1,
    )

    results = await service.sync_periods([(2026, 6)])

    assert results == [SyncOutcome("precipitation", 2026, 6, "skipped", "")]
    assert downloader.ensure_calls == []
    assert downloader.repair_calls == []


@pytest.mark.asyncio
async def test_sync_once_ingests_missing_history_and_excludes_current_month(tmp_path: Path) -> None:
    downloader = FakeDownloader()
    service = _service(
        tmp_path,
        downloader=downloader,
        categories=["precipitation"],
        history_years=1,
    )

    await service.sync_once(as_of=date(2026, 7, 9))

    assert ("precipitation", 2026, 7) not in downloader.ensure_calls
    assert ("precipitation", 2026, 6) in downloader.ensure_calls
    assert len(downloader.ensure_calls) == 12


@pytest.mark.asyncio
async def test_daily_retry_until_previous_month_becomes_available(tmp_path: Path) -> None:
    downloader = FakeDownloader()
    downloader.fail_ensure_for[("precipitation", 2026, 6)] = 1
    service = _service(
        tmp_path,
        downloader=downloader,
        categories=["precipitation"],
        history_years=1,
    )

    first = await service.sync_periods([(2026, 6)])
    second = await service.sync_periods([(2026, 6)])

    assert first[0].action == "failed"
    assert second[0].action == "ingested"
    assert downloader.ensure_calls == [
        ("precipitation", 2026, 6),
        ("precipitation", 2026, 6),
    ]


@pytest.mark.asyncio
async def test_restart_resume_skips_completed_and_retries_failed(tmp_path: Path) -> None:
    repo = FakeDatasetRepository()
    downloader = FakeDownloader()
    downloader.fail_ensure_for[("precipitation", 2026, 6)] = 1
    service = _service(
        tmp_path,
        repo=repo,
        downloader=downloader,
        categories=["precipitation"],
        history_years=1,
    )

    first = await service.sync_periods([(2026, 5), (2026, 6)])
    second = await service.sync_periods([(2026, 5), (2026, 6)])

    assert [r.action for r in first] == ["ingested", "failed"]
    assert [r.action for r in second] == ["skipped", "ingested"]
    assert downloader.ensure_calls == [
        ("precipitation", 2026, 5),
        ("precipitation", 2026, 6),
        ("precipitation", 2026, 6),
    ]


def test_next_daily_run_targets_next_01_00_in_configured_timezone() -> None:
    now = datetime(2026, 7, 9, 2, 30, tzinfo=timezone.utc)

    next_run = next_daily_run(now=now, tz_name="UTC")

    assert next_run == datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_scheduler_runs_initial_sync_on_startup(tmp_path: Path, monkeypatch) -> None:
    settings = _make_settings(
        tmp_path,
        era5_history_years=10,
        era5_sync_concurrency=2,
        era5_scheduler_timezone="UTC",
    )
    events: list[str] = []

    class StubService:
        def __init__(self, *, settings=None):
            events.append("init")

        async def sync_once(self):
            events.append("sync_once")
            return []

    async def fake_sleep(_seconds: float) -> None:
        events.append("sleep")
        raise asyncio.CancelledError

    monkeypatch.setattr("ingestion.era5.scheduler.Era5SyncService", StubService)
    monkeypatch.setattr("ingestion.era5.scheduler.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_scheduler_forever(settings)

    assert events[:3] == ["init", "sync_once", "sleep"]


@pytest.mark.asyncio
async def test_scheduler_run_once_exits_after_initial_sync(tmp_path: Path, monkeypatch) -> None:
    settings = _make_settings(
        tmp_path,
        era5_history_years=10,
        era5_sync_concurrency=2,
        era5_scheduler_timezone="UTC",
        era5_scheduler_run_once=True,
    )
    events: list[str] = []

    class StubService:
        def __init__(self, *, settings=None):
            events.append("init")

        async def sync_once(self):
            events.append("sync_once")
            return []

    async def fake_sleep(_seconds: float) -> None:
        events.append("sleep")
        raise AssertionError("sleep should not be reached")

    monkeypatch.setattr("ingestion.era5.scheduler.Era5SyncService", StubService)
    monkeypatch.setattr("ingestion.era5.scheduler.asyncio.sleep", fake_sleep)

    await run_scheduler_forever(settings)

    assert events == ["init", "sync_once"]
