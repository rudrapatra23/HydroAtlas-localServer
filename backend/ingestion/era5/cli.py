"""Era5-land ingestion cli."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

from core.config import Settings, get_settings
from infrastructure.db.session import async_session_maker
from infrastructure.repositories.postgres_dataset_repository import (
    PostgresDatasetRepository,
)
from infrastructure.storage.s3_storage_adapter import S3StorageAdapter
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from domain.entities.climate_asset import ClimateAsset
from domain.ports.dataset_repository import DatasetRepository

from ingestion.era5.downloader import DatasetHandle, Downloader
from ingestion.era5.file_service import FileService
from ingestion.era5.locks import lock_registry
from ingestion.era5.scheduler import Era5SyncService
from ingestion.era5.splitter import DEFAULT_ERA5_VARIABLES, DatasetSplitter, VARIABLE_CATEGORY

from application.precompute_service import PrecomputeService
from application.raster_computation import RasterComputation
from infrastructure.db.district_monthly_statistics_model import (
    DistrictMonthlyStatisticsModel,
)


# Default provider stamped onto every ingested asset. Kept as a module
# constant so callers can override it in one place if HydroAtlas ever
# needs to ingest from a second ERA5 source.
DEFAULT_PROVIDER = "era5-land"


def _categories() -> list[str]:
    """Return the deduplicated list of logical categories the splitter."""
    seen: list[str] = []
    for var in DEFAULT_ERA5_VARIABLES:
        category = VARIABLE_CATEGORY.get(var.name, var.name)
        if category not in seen:
            seen.append(category)
    return seen


def _resolve_variables(requested: str | None) -> list[str]:
    """Validate and resolve ``--variable`` from the cli."""
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
    """Resolve and create the era5 directories."""
    storage_root = settings.era5_storage_root_resolved()
    logs_dir = settings.era5_logs_dir_resolved()
    temp_dir = storage_root / "tmp"
    locks_dir = storage_root / "locks"
    for d in (storage_root, logs_dir, temp_dir, locks_dir):
        d.mkdir(parents=True, exist_ok=True)
    return storage_root, temp_dir


def _build_downloader(settings: Settings) -> Downloader:
    """Construct the production :class:`downloader` with s3 wired in."""
    storage_root, temp_dir = _ensure_paths(settings)
    files = FileService(storage_root=storage_root, temp_dir=temp_dir)
    splitter = DatasetSplitter()
    storage_port = S3StorageAdapter()
    return Downloader(
        settings=settings,
        files=files,
        splitter=splitter,
        storage_port=storage_port,
        locks=lock_registry,
    )


def _recent_n_months(n: int) -> list[tuple[int, int]]:
    """Return the last ``n`` (year, month) pairs ending with the current month,."""
    today = date.today()
    months: list[tuple[int, int]] = []
    year, month = today.year, today.month
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
    """Run a single ``ensure_dataset`` against a fresh repository session."""
    async with async_session_maker() as session:
        repository: DatasetRepository = PostgresDatasetRepository(session)
        handle = await downloader.ensure_dataset(
            provider=provider,
            variable=variable,
            year=year,
            month=month,
            repository=repository,
        )
    logger.info(
        "Ensured %s/%s %04d-%02d cache_hit=%s source=%s "
        "storage_key=%s bytes=%d",
        provider,
        variable,
        year,
        month,
        handle.cache_hit,
        "db" if handle.cache_hit or _exists_for(handle.storage_key) else "era5",
        handle.storage_key,
        handle.file_size,
    )
    return handle


def _exists_for(storage_key: str) -> bool:
    """Best-effort s3 existence probe used purely for log-line source tagging."""
    try:
        return S3StorageAdapter().exists(storage_key)
    except Exception:  # noqa: BLE001
        return False


async def _run_periods(
    downloader: Downloader,
    *,
    periods: list[tuple[int, int]],
    variables: list[str],
    logger: logging.Logger,
) -> dict[tuple[str, int, int], DatasetHandle]:
    """Drive ``ensure_dataset`` across the cartesian product of."""
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
    variables = _resolve_variables(args.variable)
    downloader = _build_downloader(settings)
    asyncio.run(
        _run_periods(
            downloader,
            periods=[(args.year, args.month)],
            variables=variables,
            logger=logger,
        )
    )
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    settings = get_settings()
    logger = _setup_logging(settings)
    n_months = args.months if args.months is not None else settings.era5_bootstrap_months
    targets = _recent_n_months(n_months)
    variables = _resolve_variables(args.variable)
    logger.info(
        "Bootstrapping last %d months x %d variable(s): %s x %s",
        n_months,
        len(variables),
        targets,
        variables,
    )
    downloader = _build_downloader(settings)
    asyncio.run(
        _run_periods(
            downloader, periods=targets, variables=variables, logger=logger
        )
    )
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    settings = get_settings()
    logger = _setup_logging(settings)
    targets = [(args.year, m) for m in range(1, 13)]
    variables = _resolve_variables(args.variable)
    logger.info(
        "Backfilling %d x %d variable(s): %s x %s",
        args.year,
        len(variables),
        targets,
        variables,
    )
    downloader = _build_downloader(settings)
    asyncio.run(
        _run_periods(
            downloader, periods=targets, variables=variables, logger=logger
        )
    )
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    settings = get_settings()
    logger = _setup_logging(settings)
    service = Era5SyncService(settings=settings)
    results = asyncio.run(service.sync_once())
    failed = sum(1 for r in results if r.action == "failed")
    logger.info(
        "Sync complete total=%d failed=%d history_years=%d",
        len(results),
        failed,
        settings.era5_history_years,
    )
    return 1 if failed else 0


async def _fetch_status_rows(
    variable: str | None,
) -> list[ClimateAsset]:
    """Return every asset in the ``climate_assets`` table, optionally."""
    from infrastructure.db.climate_asset_model import ClimateAssetModel

    async with async_session_maker() as session:
        # Direct query on the model so we can run a simple ordered list
        # without depending on repository.list() (which sorts by
        # created_at desc, while status wants chronological order).
        stmt = select(ClimateAssetModel)
        if variable is not None:
            stmt = stmt.where(ClimateAssetModel.variable == variable)
        stmt = stmt.order_by(
            ClimateAssetModel.variable.asc(),
            ClimateAssetModel.year.asc(),
            ClimateAssetModel.month.asc(),
        )
        result = await session.execute(stmt)
        models = result.scalars().all()

    return [
        ClimateAsset(
            id=m.id,
            provider=m.provider,
            variable=m.variable,
            year=m.year,
            month=m.month,
            storage_key=m.storage_key,
            checksum=m.checksum,
            file_size=m.file_size,
            status=m.status,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
        for m in models
    ]


async def _fetch_status_summary() -> dict[str, dict[str, int]]:
    """Return a per-variable count of assets, used as the ``status``."""
    from infrastructure.db.climate_asset_model import ClimateAssetModel

    async with async_session_maker() as session:
        stmt = select(
            ClimateAssetModel.variable,
            func.count(ClimateAssetModel.id),
        ).group_by(ClimateAssetModel.variable)
        result = await session.execute(stmt)
        return {variable: int(count) for variable, count in result.all()}


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

    print("ERA5 inventory (PostgreSQL = source of truth)")
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
    print(f"  Logs dir           : {settings.era5_logs_dir_resolved()}")
    print(f"  S3 prefix          : {settings.era5_s3_prefix}")
    print(f"  CDS dataset        : {settings.era5_dataset}")
    print(f"  CDSAPI_URL set     : {bool(settings.cdsapi_url)}")
    print(f"  CDSAPI_KEY set     : {bool(settings.cdsapi_key)}")
    print(f"  CDS creds valid    : {settings.cds_credentials_configured()}")
    print(f"  AWS region         : {settings.aws_region}")
    print(f"  S3 bucket          : {settings.s3_bucket_name}")
    db_host = settings.database_url.split("@", 1)[-1] if "@" in settings.database_url else "(none)"
    print(f"  Database URL host  : {db_host}")

    # S3 connectivity
    try:
        s3 = S3StorageAdapter()
        print(f"  S3 adapter init    : OK (bucket={s3.bucket_name})")
    except Exception as exc:  # noqa: BLE001
        print(f"  S3 adapter init    : FAIL ({exc})")

    # DB connectivity
    try:
        asyncio.run(_check_db())
        print("  DB SELECT 1        : OK")
    except Exception as exc:  # noqa: BLE001
        print(f"  DB SELECT 1        : FAIL ({exc})")
    return 0


# Precompute subcommands.
#
# These commands are the write-side of the ``district_monthly_statistics``
# table. They do not touch the routers; the on-demand
# ``RasterComputation`` path remains the only consumer of the production
# statistics endpoints.


async def _run_precompute_one(
    *,
    provider: str,
    variable: str,
    year: int,
    month: int,
    dry_run: bool,
) -> int:
    """Resolve the asset, optionally preview it, then precompute one month."""
    from infrastructure.repositories.postgres_district_monthly_statistics_repository import (
        PostgresDistrictMonthlyStatisticsRepository,
    )

    async with async_session_maker() as session:
        repository = PostgresDatasetRepository(session)
        asset = await repository.get_by_period(
            year=year, month=month, provider=provider, variable=variable
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
        dms_repo = PostgresDistrictMonthlyStatisticsRepository(session)
        already = await dms_repo.count_for_asset(asset.id)
        print(f"  rows already for asset.id : {already}")

        if dry_run:
            print("DRY-RUN: skipping precompute.")
            return 0

        storage = S3StorageAdapter()
        raster_computation = RasterComputation(repository, storage)
        # Dedicated NullPool engine for the precompute service so the
        # 8–10 minute clipping loop does not leave a pooled asyncpg
        # connection sitting idle long enough for Neon's reaper to
        # close it. Each batch upsert opens a fresh connection,
        # executes its single statement, and closes — no pool
        # staleness, no reaper kill. The shared ``async_session_maker``
        # still drives the asset lookup above (which is fast and
        # therefore pool-safe).
        from infrastructure.db.engine import build_asyncpg_url_and_connect_args
        asyncpg_url, asyncpg_connect_args = build_asyncpg_url_and_connect_args()
        precompute_engine = create_async_engine(
            asyncpg_url,
            connect_args=asyncpg_connect_args,
            poolclass=NullPool,
        )
        precompute_session_maker = async_sessionmaker(
            precompute_engine, expire_on_commit=False
        )
        try:
            service = PrecomputeService(
                session_factory=precompute_session_maker,
                storage=storage,
                raster_computation=raster_computation,
            )
            result = await service.precompute_one(
                provider=provider, variable=variable, year=year, month=month
            )
            t = result.timings
            print()
            print("Precompute result")
            print(f"  districts processed : {result.districts_processed}")
            print(f"  rows upserted       : {result.rows_upserted}")
            print(f"  s3_read_seconds     : {t.s3_read_seconds:.3f}")
            print(f"  dataset_open_seconds: {t.dataset_open_seconds:.3f}")
            print(f"  clipping_total_seconds: {t.clipping_total_seconds:.3f}")
            print(f"  db_upsert_seconds   : {t.db_upsert_seconds:.3f}")
            print(f"  total_seconds       : {t.total_seconds:.3f}")
            print(f"  peak_memory_mb      : {t.peak_memory_mb:.1f}")
            return 0
        finally:
            await precompute_engine.dispose()


def cmd_precompute(args: argparse.Namespace) -> int:
    """Precompute a single ``(provider, variable, year, month)`` triple."""
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


async def _fetch_precompute_status() -> list[tuple[str, int, int, int]]:
    """Return ``(provider, variable, year, month, row_count)`` rows."""
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
        result = await session.execute(stmt)
        return list(result.all())


def cmd_precompute_status(args: argparse.Namespace) -> int:
    """Show the current ``district_monthly_statistics`` coverage."""
    settings = get_settings()
    _setup_logging(settings)
    rows = asyncio.run(_fetch_precompute_status())
    if not rows:
        print("district_monthly_statistics: 0 rows.")
        return 0
    print(
        f"district_monthly_statistics: {sum(int(r[4]) for r in rows)} rows "
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

    p_dl = sub.add_parser(
        "download",
        help=(
            "Ensure a single (year, month) bundle exists for every category "
            "(or just --variable V)"
        ),
    )
    p_dl.add_argument("year", type=int)
    p_dl.add_argument("month", type=int)
    p_dl.add_argument(
        "--variable",
        choices=_categories(),
        default=None,
        help="Restrict to a single logical variable (default: all categories)",
    )
    p_dl.set_defaults(func=cmd_download)

    p_bs = sub.add_parser(
        "bootstrap",
        help=(
            "Ensure the last N months (default: ERA5_BOOTSTRAP_MONTHS) are "
            "present for every category"
        ),
    )
    p_bs.add_argument("months", type=int, nargs="?", default=None)
    p_bs.add_argument(
        "--variable",
        choices=_categories(),
        default=None,
        help="Restrict to a single logical variable (default: all categories)",
    )
    p_bs.set_defaults(func=cmd_bootstrap)

    p_bf = sub.add_parser(
        "backfill",
        help="Ensure all 12 months of the given year exist for every category",
    )
    p_bf.add_argument("year", type=int)
    p_bf.add_argument(
        "--variable",
        choices=_categories(),
        default=None,
        help="Restrict to a single logical variable (default: all categories)",
    )
    p_bf.set_defaults(func=cmd_backfill)

    p_sy = sub.add_parser(
        "sync",
        help=(
            "Ensure the rolling ERA5 history window exists in the current "
            "bucket using PostgreSQL + S3 reconciliation"
        ),
    )
    p_sy.set_defaults(func=cmd_sync)

    p_st = sub.add_parser(
        "status",
        help="List climate_assets rows in PostgreSQL (no manifest lookup)",
    )
    p_st.add_argument(
        "--variable",
        choices=_categories(),
        default=None,
        help="Restrict the listing to a single logical variable",
    )
    p_st.set_defaults(func=cmd_status)

    p_dr = sub.add_parser("doctor", help="Check CDS / S3 / DB connectivity")
    p_dr.set_defaults(func=cmd_doctor)

    p_pc = sub.add_parser(
        "precompute",
        help=(
            "Precompute district_monthly_statistics for a single "
            "(provider, variable, year, month). Does not iterate "
            "automatically; re-invoke for each period."
        ),
    )
    p_pc.add_argument("--provider", default=DEFAULT_PROVIDER, help=DEFAULT_PROVIDER)
    p_pc.add_argument(
        "--variable",
        required=True,
        choices=_categories(),
        help="Logical variable category",
    )
    p_pc.add_argument("--year", type=int, required=True)
    p_pc.add_argument("--month", type=int, required=True, choices=range(1, 13))
    p_pc.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the asset and print it; do not touch S3 or PostgreSQL",
    )
    p_pc.set_defaults(func=cmd_precompute)

    p_ps = sub.add_parser(
        "precompute-status",
        help="Show district_monthly_statistics row counts per (provider, variable, year, month)",
    )
    p_ps.set_defaults(func=cmd_precompute_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
