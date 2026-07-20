"""End-to-end source-of-truth verification."""

from __future__ import annotations

import asyncio
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.config import Settings  # noqa: E402
from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus  # noqa: E402
from ingestion.era5.downloader import Downloader  # noqa: E402
from ingestion.era5.file_service import FileService  # noqa: E402
from ingestion.era5.locks import LockRegistry  # noqa: E402
from ingestion.era5.splitter import SplitFile  # noqa: E402
from tests.test_ingestion_era5 import (  # noqa: E402
    FakeCdsClient,
    FakeDatasetRepository,
    FakeSplitter,
    FakeStoragePort,
    _make_split_files,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        app_name="verify",
        version="0.0.0",
        environment="development",
        log_level="INFO",
        aws_region="us-east-1",
        aws_access_key_id="AKIA-VERIFY",
        aws_secret_access_key="verify-secret",
        s3_bucket_name="verify-bucket",
        s3_endpoint_url="https://s3.verify",
        database_url="postgresql://verify:verify@localhost/verify",
        era5_storage_root=tmp_path / "storage",
        era5_logs_dir=tmp_path / "logs",
        era5_s3_prefix="era5-land-verify",
        era5_dataset="reanalysis-era5-land-monthly-means",
        era5_max_months=480,
        era5_retry_attempts=3,
        era5_retry_base_seconds=0.0,
        era5_bootstrap_months=2,
        cdsapi_url=None,
        cdsapi_key=None,
    )


def _build(settings: Settings, splits_for: dict[tuple[int, int], list[SplitFile]]):
    """Build a downloader where each (year, month) gets its own pre-baked."""
    storage_root = settings.era5_storage_root_resolved()
    temp_dir = storage_root / "tmp"
    files = FileService(storage_root=storage_root, temp_dir=temp_dir)
    cds = FakeCdsClient()
    storage_port = FakeStoragePort()
    splitter = _SplitterRouter(splits_for)
    downloader = Downloader(
        settings=settings,
        files=files,
        splitter=splitter,  # type: ignore[arg-type]
        storage_port=storage_port,
        locks=LockRegistry(),
        cds_client=cds,
    )
    return downloader, files, cds, storage_port


class _SplitterRouter:
    """Picks a per-call fakesplitter based on (year, month) so concurrent."""

    def __init__(self, splits_for: dict[tuple[int, int], list[SplitFile]]):
        self._splits_for = splits_for

    def split(self, source: Path, year: int, month: int, temp_dir: Path):
        splits = self._splits_for.get((year, month), [])
        return FakeSplitter(splits=splits).split(source, year, month, temp_dir)


async def _ensure(
    downloader: Downloader,
    repository: FakeDatasetRepository,
    *,
    provider: str,
    variable: str,
    year: int,
    month: int,
):
    return await downloader.ensure_dataset(
        provider=provider,
        variable=variable,
        year=year,
        month=month,
        repository=repository,
    )


async def scenario_1_bootstrap_populates_rows(tmp_path: Path) -> int:
    """Bootstrap populates rows for every (variable, year, month)."""
    settings = _settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()
    target_year = 2024
    target_month = 5
    variables = ("precipitation", "soil_moisture", "surface_runoff")

    splits_for: dict[tuple[int, int], list[SplitFile]] = {}
    for var in variables:
        splits_for[(target_year, target_month)] = (
            splits_for.get((target_year, target_month), [])
            + _make_split_files(storage_root, target_year, target_month, only=var)
        )

    downloader, files, cds, storage_port = _build(settings, splits_for)
    repository = FakeDatasetRepository()

    for var in variables:
        await _ensure(
            downloader,
            repository,
            provider="era5-land",
            variable=var,
            year=target_year,
            month=target_month,
        )

    if len(repository.saved) != len(variables):
        return 1
    if len(cds.requests) != len(variables):
        return 1
    if len(storage_port.uploads) != len(variables):
        return 1

    # Per-variable cache file exists for each.
    for var in variables:
        cp = files.cache_path_for("era5-land", var, target_year, target_month)
        if not cp.exists():
            return 1
    return 0


async def scenario_2_cache_wipe_preserves_db(tmp_path: Path) -> int:
    """Deleting data/era5/cache/ does not touch postgresql rows."""
    settings = _settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()
    splits_for: dict[tuple[int, int], list[SplitFile]] = {}
    splits_for[(2024, 5)] = _make_split_files(
        storage_root, 2024, 5, only="precipitation"
    )
    downloader, files, _cds, _storage_port = _build(settings, splits_for)
    repository = FakeDatasetRepository()

    await _ensure(
        downloader,
        repository,
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=5,
    )
    saved_count_before = len(repository.saved)
    saved_id_before = repository.saved[0].id

    shutil.rmtree(files.cache_root)

    if len(repository.saved) != saved_count_before:
        return 1
    if repository.saved[0].id != saved_id_before:
        return 1
    return 0


async def scenario_3_status_reports_postgres(tmp_path: Path) -> int:
    """``status``-style query against the repository surfaces every row."""
    settings = _settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()
    splits_for: dict[tuple[int, int], list[SplitFile]] = {}
    splits_for[(2024, 5)] = (
        _make_split_files(storage_root, 2024, 5, only="precipitation")
        + _make_split_files(storage_root, 2024, 5, only="soil_moisture")
    )
    downloader, files, _cds, _storage_port = _build(settings, splits_for)
    repository = FakeDatasetRepository()

    for var in ("precipitation", "soil_moisture"):
        await _ensure(
            downloader,
            repository,
            provider="era5-land",
            variable=var,
            year=2024,
            month=5,
        )

    rows = await repository.list()
    by_var = {row.variable for row in rows}
    if by_var != {"precipitation", "soil_moisture"}:
        return 1
    return 0


async def scenario_4_rerun_after_cache_wipe_skips_cds(tmp_path: Path) -> int:
    """Re-running ensure_dataset with the cache wiped but the db row."""
    settings = _settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()
    splits_for: dict[tuple[int, int], list[SplitFile]] = {}
    splits_for[(2024, 5)] = _make_split_files(
        storage_root, 2024, 5, only="precipitation"
    )
    downloader, files, cds, storage_port = _build(settings, splits_for)
    repository = FakeDatasetRepository()

    # First write populates DB + S3 + cache.
    await _ensure(
        downloader,
        repository,
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=5,
    )
    cds_calls_after_first = len(cds.requests)
    s3_uploads_after_first = len(storage_port.uploads)
    s3_downloads_after_first = len(storage_port.downloads)

    # Wipe the cache so the next call must go to S3, not the cache.
    shutil.rmtree(files.cache_root)

    # Re-run; the row is still in the repository.
    handle = await _ensure(
        downloader,
        repository,
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=5,
    )

    # CDS must NOT have been called again.
    if len(cds.requests) != cds_calls_after_first:
        return 1
    # No new S3 uploads (the object is already in S3).
    if len(storage_port.uploads) != s3_uploads_after_first:
        return 1
    # Exactly one new S3 download restored the cache file.
    if len(storage_port.downloads) != s3_downloads_after_first + 1:
        return 1
    # The local cache file is back.
    if not files.cache_path_for("era5-land", "precipitation", 2024, 5).exists():
        return 1
    if handle.cache_hit is not False:
        return 1
    return 0


async def scenario_5_drop_rows_triggers_cds_again(tmp_path: Path) -> int:
    """Dropping the db row and the cache forces a fresh cds fetch."""
    settings = _settings(tmp_path)
    storage_root = settings.era5_storage_root_resolved()
    splits_for: dict[tuple[int, int], list[SplitFile]] = {}
    splits_for[(2024, 5)] = _make_split_files(
        storage_root, 2024, 5, only="precipitation"
    )
    downloader, files, cds, storage_port = _build(settings, splits_for)
    repository = FakeDatasetRepository()

    await _ensure(
        downloader,
        repository,
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=5,
    )
    cds_calls_after_first = len(cds.requests)
    s3_uploads_after_first = len(storage_port.uploads)

    # Drop the DB row (clear the fake repository) and wipe the cache.
    repository._rows.clear()  # type: ignore[attr-defined]
    repository.saved.clear()  # type: ignore[attr-defined]
    shutil.rmtree(files.cache_root)

    await _ensure(
        downloader,
        repository,
        provider="era5-land",
        variable="precipitation",
        year=2024,
        month=5,
    )

    if len(cds.requests) != cds_calls_after_first + 1:
        return 1
    if len(storage_port.uploads) != s3_uploads_after_first + 1:
        return 1
    if not files.cache_path_for("era5-land", "precipitation", 2024, 5).exists():
        return 1
    return 0


async def _main() -> int:
    scenarios = [
        ("scenario_1_bootstrap_populates_rows", scenario_1_bootstrap_populates_rows),
        ("scenario_2_cache_wipe_preserves_db", scenario_2_cache_wipe_preserves_db),
        ("scenario_3_status_reports_postgres", scenario_3_status_reports_postgres),
        ("scenario_4_rerun_after_cache_wipe_skips_cds", scenario_4_rerun_after_cache_wipe_skips_cds),
        ("scenario_5_drop_rows_triggers_cds_again", scenario_5_drop_rows_triggers_cds_again),
    ]

    errors = 0
    for name, fn in scenarios:
        tmp = Path("/tmp") / f"verify_{name}_{uuid.uuid4().hex[:6]}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            rc = await fn(tmp)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL  {name}  raised: {exc}")
            errors += 1
            shutil.rmtree(tmp, ignore_errors=True)
            continue
        if rc == 0:
            print(f"PASS  {name}")
        else:
            print(f"FAIL  {name}")
            errors += 1
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if errors == 0:
        print("ALL SCENARIOS PASSED — PostgreSQL is the source of truth.")
    else:
        print(f"{errors} SCENARIO(S) FAILED")
    return errors


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
