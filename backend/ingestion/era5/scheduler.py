from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import AsyncContextManager, Callable
from zoneinfo import ZoneInfo

from core.config import Settings, get_settings
from infrastructure.db.session import async_session_maker
from infrastructure.repositories.postgres_dataset_repository import (
    PostgresDatasetRepository,
)
from infrastructure.storage.local_storage_adapter import LocalStorageAdapter

from ingestion.era5.downloader import Downloader
from ingestion.era5.file_service import FileService
from ingestion.era5.locks import lock_registry
from ingestion.era5.splitter import DEFAULT_ERA5_VARIABLES, DatasetSplitter, VARIABLE_CATEGORY
from ingestion.era5.validation import previous_month


logger = logging.getLogger("ingestion.era5.scheduler")


def logical_categories() -> list[str]:
    seen: list[str] = []
    for var in DEFAULT_ERA5_VARIABLES:
        category = VARIABLE_CATEGORY.get(var.name, var.name)
        if category not in seen:
            seen.append(category)
    return seen


def history_months(
    *,
    as_of: date,
    history_years: int,
) -> list[tuple[int, int]]:
    """Return the rolling historical month window ending at previous month."""
    if history_years <= 0:
        return []
    total_months = history_years * 12
    year, month = previous_month(as_of)
    months: list[tuple[int, int]] = []
    for _ in range(total_months):
        months.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(months))


def next_daily_run(
    *,
    now: datetime,
    tz_name: str,
    hour: int = 1,
    minute: int = 0,
) -> datetime:
    tz = ZoneInfo(tz_name)
    local_now = now.astimezone(tz)
    candidate = datetime.combine(
        local_now.date(),
        time(hour=hour, minute=minute, tzinfo=tz),
    )
    if local_now >= candidate:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


@dataclass(frozen=True)
class SyncOutcome:
    variable: str
    year: int
    month: int
    action: str
    detail: str = ""


def _build_downloader(settings: Settings) -> Downloader:
    storage_root = settings.era5_storage_root_resolved()
    logs_dir = settings.era5_logs_dir_resolved()
    temp_dir = storage_root / "tmp"
    locks_dir = storage_root / "locks"
    for d in (storage_root, logs_dir, temp_dir, locks_dir):
        d.mkdir(parents=True, exist_ok=True)
    files = FileService(storage_root=storage_root, temp_dir=temp_dir)
    splitter = DatasetSplitter()
    storage_port = LocalStorageAdapter()
    return Downloader(
        settings=settings,
        files=files,
        splitter=splitter,
        storage_port=storage_port,
        locks=lock_registry,
    )


class Era5SyncService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        downloader: Downloader | None = None,
        storage: LocalStorageAdapter | None = None,
        categories: list[str] | None = None,
        concurrency: int | None = None,
        repository_factory: Callable[[], AsyncContextManager[PostgresDatasetRepository]] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._storage = storage or LocalStorageAdapter()
        self._downloader = downloader or _build_downloader(self._settings)
        self._categories = categories or logical_categories()
        self._concurrency = max(1, concurrency or self._settings.era5_sync_concurrency)
        self._repository_factory = repository_factory or self._default_repository_factory

    def _default_repository_factory(self) -> AsyncContextManager[PostgresDatasetRepository]:
        return _RepositoryContext()

    async def sync_once(
        self,
        *,
        as_of: date | None = None,
    ) -> list[SyncOutcome]:
        target_date = as_of or datetime.now(ZoneInfo(self._settings.era5_scheduler_timezone)).date()
        periods = history_months(
            as_of=target_date,
            history_years=self._settings.era5_history_years,
        )
        return await self.sync_periods(periods)

    async def sync_periods(
        self,
        periods: list[tuple[int, int]],
    ) -> list[SyncOutcome]:
        if not periods:
            return []

        sem = asyncio.Semaphore(self._concurrency)
        tasks = [
            asyncio.create_task(self._sync_target(sem, variable, year, month))
            for variable in self._categories
            for year, month in periods
        ]
        return await asyncio.gather(*tasks)

    async def _sync_target(
        self,
        sem: asyncio.Semaphore,
        variable: str,
        year: int,
        month: int,
    ) -> SyncOutcome:
        async with sem:
            try:
                async with self._repository_factory() as repository:
                    asset = await repository.get_by_period(
                        year=year,
                        month=month,
                        provider="era5-land",
                        variable=variable,
                    )
                    if asset is not None:
                        exists = await asyncio.to_thread(
                            self._storage.exists, asset.storage_key
                        )
                        if exists:
                            return SyncOutcome(variable, year, month, "skipped")
                        await self._downloader.repair_registered_asset(
                            asset=asset,
                            repository=repository,
                        )
                        return SyncOutcome(variable, year, month, "repaired")

                    await self._downloader.ensure_dataset(
                        provider="era5-land",
                        variable=variable,
                        year=year,
                        month=month,
                        repository=repository,
                    )
                    return SyncOutcome(variable, year, month, "ingested")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "era5.sync target=%s/%04d-%02d action=failed error=%s",
                    variable,
                    year,
                    month,
                    exc,
                )
                return SyncOutcome(variable, year, month, "failed", str(exc))


class _RepositoryContext:
    async def __aenter__(self) -> PostgresDatasetRepository:
        self._session = async_session_maker()
        session = await self._session.__aenter__()
        self._repo = PostgresDatasetRepository(session)
        return self._repo

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._session.__aexit__(exc_type, exc, tb)


async def run_scheduler_forever(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    service = Era5SyncService(settings=settings)

    logger.info(
        "era5.scheduler.start timezone=%s history_years=%d concurrency=%d",
        settings.era5_scheduler_timezone,
        settings.era5_history_years,
        settings.era5_sync_concurrency,
    )
    startup_results = await service.sync_once()
    logger.info(
        "era5.scheduler.initial_sync complete total=%d failed=%d",
        len(startup_results),
        sum(1 for r in startup_results if r.action == "failed"),
    )
    if settings.era5_scheduler_run_once:
        logger.info("era5.scheduler.run_once complete")
        return

    while True:
        now_utc = datetime.now(timezone.utc)
        next_run = next_daily_run(
            now=now_utc,
            tz_name=settings.era5_scheduler_timezone,
        )
        sleep_seconds = max(0.0, (next_run - now_utc).total_seconds())
        logger.info(
            "era5.scheduler.sleep next_run_utc=%s sleep_seconds=%.1f",
            next_run.isoformat(),
            sleep_seconds,
        )
        await asyncio.sleep(sleep_seconds)
        results = await service.sync_once()
        logger.info(
            "era5.scheduler.daily_sync complete total=%d failed=%d",
            len(results),
            sum(1 for r in results if r.action == "failed"),
        )


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(run_scheduler_forever())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
