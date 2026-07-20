"""ERA5-Land ingestion CLI for the local HydroAtlas deployment."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import func, select, text

from application.precompute_service import PrecomputeService
from application.raster_computation import RasterComputation
from core.config import Settings, get_settings
from domain.entities.climate_asset import ClimateAsset
from domain.ports.dataset_repository import DatasetRepository
from infrastructure.db.district_monthly_statistics_model import (
    DistrictMonthlyStatisticsModel,
)
from infrastructure.db.session import async_session_maker
from infrastructure.repositories.postgres_dataset_repository import (
    SqlAlchemyDatasetRepository,
)
from infrastructure.repositories.postgres_district_monthly_statistics_repository import (
    SqlAlchemyDistrictMonthlyStatisticsRepository,
)
from infrastructure.storage.local_storage_adapter import LocalStorageAdapter
from ingestion.era5.downloader import DatasetHandle, Downloader
from ingestion.era5.file_service import FileService
from ingestion.era5.locks import lock_registry
from ingestion.era5.scheduler import Era5SyncService
from ingestion.era5.splitter import (
    DEFAULT_ERA5_VARIABLES,
    DatasetSplitter,
    VARIABLE_CATEGORY,
)


DEFAULT_PROVIDER = "era5-land"


def _categories() -> list[str]:
    seen: list[str] = []
    for var in DEFAULT_ERA5_VARIABLES:
        category = VARIABLE_CATEGORY.get(var.name, var.name)
        if category not in seen:
            seen.append(category)
    return seen


def _resolve_variables(requested: str | None) -> list[str]:
    if requested is None:
        return _categories()
    if requested not in _categories():
        raise SystemExit(
            f"unknown variable {requested!r}; expected one of {_categories()}"
        )
    return [requested]


def _setup_logging(settings: Settings) -> logging.Logger:
    logger = logging.getLogger("ingestion.era5")
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logger.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    return logger


def _ensure_paths(settings: Settings) -> tuple[Path, Path]:
    storage_root = settings.era5_storage_root_resolved()
    logs_dir = settings.era5_logs_dir_resolved()
    temp_dir = settings.raster_cache_root_resolved() / "tmp"
    locks_dir = settings.raster_cache_root_resolved() / "locks"
    for path in (storage_root, logs_dir, temp_dir, locks_dir):
        path.mkdir(parents=True, exist_ok=True)
    return storage_root, temp_dir


def _build_downloader(settings: Settings) -> Downloader:
    storage_root, temp_dir = _ensure_paths(settings)
    files = FileService(
        storage_root=storage_root,
        temp_dir=temp_dir,
        cache_root=settings.raster_cache_root_resolved(),
    )
    return Downloader(
        settings=settings,
        files=files,
        splitter=DatasetSplitter(),
        storage_port=LocalStorageAdapter(),
        locks=lock_registry,
    )


def _recent_n_months(n: int) -> list[tuple[int, int]]:
    today = date.today()
    year, month = today.year, today.month
    # ERA5-Land monthly data lags behind real time; the current month
    # is never available yet, so start from the previous month.
    month -= 1
    if month == 0:
        month = 12
        year -= 1

    months: list[tuple[int, int]] = []
    for _ in range(n):
        months.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(months))


async def _ensure_one(
    downloader: Downloader,
    *,
    provider: str,
    variable: str,
    year: int,
    month: int,
    logger: logging.Logger,
) -> DatasetHandle:
    async with async_session_maker() as session:
        repository: DatasetRepository = SqlAlchemyDatasetRepository(session)
        handle = await downloader.ensure_dataset(
            provider=provider,
            variable=variable,
            year=year,
            month=month,
            repository=repository,
        )
    logger.info(
        "Ensured %s/%s %04d-%02d cache_hit=%s source=%s storage_key=%s bytes=%d",
        provider,
        variable,
        year,
        month,
        handle.cache_hit,
        "local-cache" if handle.cache_hit or _exists_for(handle.storage_key) else "era5",
        handle.storage_key,
        handle.file_size,
    )
    return handle


def _exists_for(storage_key: str) -> bool:
    try:
        return LocalStorageAdapter().exists(storage_key)
    except Exception:
        return False


async def _run_periods(
    downloader: Downloader,
    *,
    periods: list[tuple[int, int]],
    variables: list[str],
    logger: logging.Logger,
) -> dict[tuple[str, int, int], DatasetHandle]:
    results: dict[tuple[str, int, int], DatasetHandle] = {}
    for variable in variables:
        for year, month in periods:
            handle = await _ensure_one(
                downloader,
                provider=DEFAULT_PROVIDER,
                variable=variable,
                year=year,
                month=month,
                logger=logger,
            )
            results[(variable, year, month)] = handle
    return results


def cmd_download(args: argparse.Namespace) -> int:
    settings = get_settings()
    logger = _setup_logging(settings)
    downloader = _build_downloader(settings)
    asyncio.run(
        _run_periods(
            downloader,
            periods=[(args.year, args.month)],
            variables=_resolve_variables(args.variable),
            logger=logger,
        )
    )
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    settings = get_settings()
    logger = _setup_logging(settings)
    downloader = _build_downloader(settings)
    months = args.months if args.months is not None else settings.era5_bootstrap_months
    asyncio.run(
        _run_periods(
            downloader,
            periods=_recent_n_months(months),
            variables=_resolve_variables(args.variable),
            logger=logger,
        )
    )
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    settings = get_settings()
    logger = _setup_logging(settings)
    downloader = _build_downloader(settings)
    asyncio.run(
        _run_periods(
            downloader,
            periods=[(args.year, month) for month in range(1, 13)],
            variables=_resolve_variables(args.variable),
            logger=logger,
        )
    )
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    settings = get_settings()
    logger = _setup_logging(settings)
    results = asyncio.run(Era5SyncService(settings=settings).sync_once())
    failed = sum(1 for result in results if result.action == "failed")
    logger.info(
        "Sync complete total=%d failed=%d history_years=%d",
        len(results),
        failed,
        settings.era5_history_years,
    )
    return 1 if failed else 0


async def _fetch_status_rows(variable: str | None) -> list[ClimateAsset]:
    from infrastructure.db.climate_asset_model import ClimateAssetModel

    async with async_session_maker() as session:
        stmt = select(ClimateAssetModel)
        if variable is not None:
            stmt = stmt.where(ClimateAssetModel.variable == variable)
        stmt = stmt.order_by(
            ClimateAssetModel.variable.asc(),
            ClimateAssetModel.year.asc(),
            ClimateAssetModel.month.asc(),
        )
        models = (await session.execute(stmt)).scalars().all()

    return [
        ClimateAsset(
            id=model.id,
            provider=model.provider,
            variable=model.variable,
            year=model.year,
            month=model.month,
            storage_key=model.storage_key,
            checksum=model.checksum,
            file_size=model.file_size,
            status=model.status,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
        for model in models
    ]


async def _fetch_status_summary() -> dict[str, int]:
    from infrastructure.db.climate_asset_model import ClimateAssetModel

    async with async_session_maker() as session:
        stmt = select(
            ClimateAssetModel.variable,
            func.count(ClimateAssetModel.id),
        ).group_by(ClimateAssetModel.variable)
        rows = (await session.execute(stmt)).all()
    return {variable: int(count) for variable, count in rows}


def cmd_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    _setup_logging(settings)
    storage_root, _ = _ensure_paths(settings)

    variable = args.variable
    if variable is not None and variable not in _categories():
        raise SystemExit(
            f"unknown variable {variable!r}; expected one of {_categories()}"
        )

    summary = asyncio.run(_fetch_status_summary())
    rows = asyncio.run(_fetch_status_rows(variable))

    print("ERA5 inventory (SQLite metadata + local filesystem)")
    print(f"  Storage root : {storage_root}")
    print(f"  Provider     : {DEFAULT_PROVIDER}")
    if variable is not None:
        print(f"  Filter       : variable={variable}")
    print()

    if not summary:
        print("  No rows in climate_assets.")
        return 0

    print("  Per-variable counts:")
    for var in _categories():
        if var in summary:
            print(f"    {var:<18s} {summary[var]:>6d}")
    print()
    print(f"  {'variable':<18s} {'period':<9s} {'bytes':>12s}  storage_key")
    print(f"  {'-' * 18} {'-' * 9} {'-' * 12}  {'-' * 32}")
    for asset in rows:
        print(
            f"  {asset.variable:<18s} "
            f"{asset.year:04d}-{asset.month:02d}  "
            f"{asset.file_size:>12d}  {asset.storage_key}"
        )
    return 0


async def _check_db() -> None:
    async with async_session_maker() as session:
        await session.execute(text("SELECT 1"))


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = get_settings()
    _setup_logging(settings)
    print("ERA5 ingestion doctor")
    print(f"  Storage root       : {settings.era5_storage_root_resolved()}")
    print(f"  Cache root         : {settings.raster_cache_root_resolved()}")
    print(f"  Logs dir           : {settings.era5_logs_dir_resolved()}")
    print(f"  ERA5 prefix        : {settings.era5_storage_prefix}")
    print(f"  CDS dataset        : {settings.era5_dataset}")
    print(f"  CDSAPI_URL set     : {bool(settings.cdsapi_url)}")
    print(f"  CDSAPI_KEY set     : {bool(settings.cdsapi_key)}")
    print(f"  CDS creds valid    : {settings.cds_credentials_configured()}")
    print(f"  Database URL       : {settings.database_url}")

    try:
        storage = LocalStorageAdapter()
        print(f"  Storage adapter    : OK ({storage.storage_dir})")
    except Exception as exc:
        print(f"  Storage adapter    : FAIL ({exc})")

    try:
        asyncio.run(_check_db())
        print("  DB SELECT 1        : OK")
    except Exception as exc:
        print(f"  DB SELECT 1        : FAIL ({exc})")
    return 0


async def _run_precompute_one(
    *,
    provider: str,
    variable: str,
    year: int,
    month: int,
    dry_run: bool,
) -> int:
    async with async_session_maker() as session:
        repository = SqlAlchemyDatasetRepository(session)
        asset = await repository.get_by_period(
            year=year,
            month=month,
            provider=provider,
            variable=variable,
        )
        if asset is None:
            print(
                f"FAIL  no climate_assets row for "
                f"{provider}/{variable}/{year:04d}-{month:02d}"
            )
            print(
                "      Ingest this period first: "
                f"python -m ingestion.era5.cli download {year} {month} "
                f"--variable {variable}"
            )
            return 1

        print("Precompute target")
        print(f"  provider         : {provider}")
        print(f"  variable         : {variable}")
        print(f"  year             : {year}")
        print(f"  month            : {month:02d}")
        print(f"  asset.id         : {asset.id}")
        print(f"  asset.storage_key: {asset.storage_key}")
        print(f"  asset.checksum   : {asset.checksum}")
        print(f"  asset.file_size  : {asset.file_size} bytes")
        print(f"  asset.status     : {asset.status.value}")
        dms_repo = SqlAlchemyDistrictMonthlyStatisticsRepository(session)
        print(f"  rows already for asset.id : {await dms_repo.count_for_asset(asset.id)}")

        if dry_run:
            print("DRY-RUN: skipping precompute.")
            return 0

        service = PrecomputeService(
            session_factory=async_session_maker,
            storage=LocalStorageAdapter(),
            raster_computation=RasterComputation(repository, LocalStorageAdapter()),
        )
        result = await service.precompute_one(
            provider=provider,
            variable=variable,
            year=year,
            month=month,
        )
        timings = result.timings
        print()
        print("Precompute result")
        print(f"  districts processed : {result.districts_processed}")
        print(f"  rows upserted       : {result.rows_upserted}")
        print(f"  local_read_seconds  : {timings.s3_read_seconds:.3f}")
        print(f"  dataset_open_seconds: {timings.dataset_open_seconds:.3f}")
        print(f"  clipping_total_seconds: {timings.clipping_total_seconds:.3f}")
        print(f"  db_upsert_seconds   : {timings.db_upsert_seconds:.3f}")
        print(f"  total_seconds       : {timings.total_seconds:.3f}")
        print(f"  peak_memory_mb      : {timings.peak_memory_mb:.1f}")
        return 0


def cmd_precompute(args: argparse.Namespace) -> int:
    settings = get_settings()
    _setup_logging(settings)
    return asyncio.run(
        _run_precompute_one(
            provider=args.provider,
            variable=args.variable,
            year=args.year,
            month=args.month,
            dry_run=args.dry_run,
        )
    )


async def _fetch_precompute_status() -> list[tuple[str, str, int, int, int]]:
    async with async_session_maker() as session:
        stmt = select(
            DistrictMonthlyStatisticsModel.provider,
            DistrictMonthlyStatisticsModel.variable,
            DistrictMonthlyStatisticsModel.year,
            DistrictMonthlyStatisticsModel.month,
            func.count(DistrictMonthlyStatisticsModel.id),
        ).group_by(
            DistrictMonthlyStatisticsModel.provider,
            DistrictMonthlyStatisticsModel.variable,
            DistrictMonthlyStatisticsModel.year,
            DistrictMonthlyStatisticsModel.month,
        ).order_by(
            DistrictMonthlyStatisticsModel.provider,
            DistrictMonthlyStatisticsModel.variable,
            DistrictMonthlyStatisticsModel.year,
            DistrictMonthlyStatisticsModel.month,
        )
        return list((await session.execute(stmt)).all())


def cmd_precompute_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    _setup_logging(settings)
    rows = asyncio.run(_fetch_precompute_status())
    if not rows:
        print("district_monthly_statistics: 0 rows.")
        return 0
    print(
        f"district_monthly_statistics: {sum(int(row[4]) for row in rows)} rows "
        f"across {len(rows)} (provider, variable, year, month) groups"
    )
    print()
    print(f"  {'provider':<10s} {'variable':<18s} {'period':<9s} {'rows':>6s}")
    print(f"  {'-' * 10} {'-' * 18} {'-' * 9} {'-' * 6}")
    for provider, variable, year, month, count in rows:
        print(
            f"  {provider:<10s} {variable:<18s} "
            f"{year:04d}-{month:02d}  {count:>6d}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ingestion.era5.cli",
        description="ERA5-Land ingestion CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_dl = sub.add_parser("download", help="Ensure one month exists locally")
    p_dl.add_argument("year", type=int)
    p_dl.add_argument("month", type=int)
    p_dl.add_argument("--variable", choices=_categories(), default=None)
    p_dl.set_defaults(func=cmd_download)

    p_bs = sub.add_parser("bootstrap", help="Ensure the last N months exist locally")
    p_bs.add_argument("months", type=int, nargs="?", default=None)
    p_bs.add_argument("--variable", choices=_categories(), default=None)
    p_bs.set_defaults(func=cmd_bootstrap)

    p_bf = sub.add_parser("backfill", help="Ensure all 12 months of a year exist")
    p_bf.add_argument("year", type=int)
    p_bf.add_argument("--variable", choices=_categories(), default=None)
    p_bf.set_defaults(func=cmd_backfill)

    p_sy = sub.add_parser(
        "sync",
        help="Reconcile the rolling ERA5 history window against local storage",
    )
    p_sy.set_defaults(func=cmd_sync)

    p_st = sub.add_parser("status", help="List climate_assets rows in SQLite")
    p_st.add_argument("--variable", choices=_categories(), default=None)
    p_st.set_defaults(func=cmd_status)

    p_dr = sub.add_parser(
        "doctor", help="Check CDS, local storage, and SQLite connectivity"
    )
    p_dr.set_defaults(func=cmd_doctor)

    p_pc = sub.add_parser(
        "precompute",
        help="Precompute district_monthly_statistics for one month",
    )
    p_pc.add_argument("--provider", default=DEFAULT_PROVIDER, help=DEFAULT_PROVIDER)
    p_pc.add_argument("--variable", required=True, choices=_categories())
    p_pc.add_argument("--year", type=int, required=True)
    p_pc.add_argument("--month", type=int, required=True, choices=range(1, 13))
    p_pc.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the asset and print it without writing local results",
    )
    p_pc.set_defaults(func=cmd_precompute)

    p_ps = sub.add_parser(
        "precompute-status",
        help="Show district_monthly_statistics row counts per month",
    )
    p_ps.set_defaults(func=cmd_precompute_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
