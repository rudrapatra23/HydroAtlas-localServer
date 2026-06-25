#!/usr/bin/env python3
"""CLI for ingesting ERA5 climate datasets."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables
load_dotenv(Path(__file__).parent / ".env")

from application.dataset_service import DatasetService
from application.dto.requests import BootstrapRequest, DownloadRequest
from application.dto.responses import DownloadResponse
from application.providers.era5_provider import ERA5Provider
from core.config import get_settings
from infrastructure.db.session import async_session_maker
from infrastructure.repositories.postgres_dataset_repository import PostgresDatasetRepository
from infrastructure.storage.s3_storage_adapter import S3StorageAdapter


async def bootstrap_provider(provider: ERA5Provider) -> None:
    """Bootstrap the ERA5 provider with configuration."""
    import tempfile
    from pathlib import Path

    temp_base = Path(tempfile.gettempdir()) / "era5_fetch"
    temp_base.mkdir(parents=True, exist_ok=True)

    config = {
        "storage_root": str(temp_base / "storage"),
        "storage_dir": str(temp_base / "data"),
        "logs_dir": str(temp_base / "logs"),
        "manifest_path": str(temp_base / "manifest.json"),
        "temp_dir": str(temp_base / "temp"),
        "locks_dir": str(temp_base / "locks"),
        "dataset": "reanalysis-era5-land-monthly-means",
    }
    await provider.bootstrap(BootstrapRequest(config=config))


async def ingest_dataset(
    provider: str,
    variable: str,
    year: int,
    month: int,
    region: tuple[float, float, float, float] | None = None,
) -> DownloadResponse:
    """Ingest a climate dataset."""
    storage = S3StorageAdapter()

    async with async_session_maker() as session:
        repository = PostgresDatasetRepository(session)
        era5_provider = ERA5Provider()

        await bootstrap_provider(era5_provider)

        service = DatasetService(repository, storage, era5_provider)

        request = DownloadRequest(
            provider=provider,
            variable=variable,
            year=year,
            month=month,
            region=region,
        )

        return await service.download_and_register(request)


def generate_month_range(from_year: int, from_month: int, to_year: int, to_month: int):
    """Generate all (year, month) tuples month-by-month inclusively."""
    months = []
    current_year = from_year
    current_month = from_month

    while (current_year < to_year) or (current_year == to_year and current_month <= to_month):
        months.append((current_year, current_month))
        current_month += 1
        if current_month > 12:
            current_month = 1
            current_year += 1
    return months


async def bootstrap_ingestion(
    from_year: int = 2020,
    from_month: int = 1,
    to_year: int = 2025,
    to_month: int = 12,
) -> dict:
    """Bootstrap ingestion for all months from start date to end date."""
    provider = "era5-land"
    variable = "precipitation"

    stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    failed_months = []
    start_time = time.time()

    months = generate_month_range(from_year, from_month, to_year, to_month)
    total_months = len(months)

    for idx, (year, month) in enumerate(months, 1):
        period_str = f"{year}-{month:02d}"

        try:
            async with async_session_maker() as session:
                repository = PostgresDatasetRepository(session)

                existing = await repository.get_by_period(year, month, provider, variable)
                if existing:
                    print(f"[{idx}/{total_months}] {period_str} ✓ Skipped")
                    stats["skipped"] += 1
                    continue

            storage = S3StorageAdapter()
            era5_provider = ERA5Provider()
            await bootstrap_provider(era5_provider)

            response = None
            max_attempts = 2
            for attempt in range(1, max_attempts + 1):
                try:
                    async with async_session_maker() as session:
                        repository = PostgresDatasetRepository(session)
                        service = DatasetService(repository, storage, era5_provider)

                        request = DownloadRequest(
                            provider=provider,
                            variable=variable,
                            year=year,
                            month=month,
                        )

                        response = await service.download_and_register(request)
                        break  # Success, exit retry loop
                except Exception as e:
                    if attempt < max_attempts:
                        print(f"[{idx}/{total_months}] {period_str} Retrying (attempt {attempt}/{max_attempts})...")
                        time.sleep(2)  # Small delay before retry
                    else:
                        raise  # Re-raise on last attempt

            if response and response.success:
                print(f"[{idx}/{total_months}] {period_str} ✓ Downloaded")
                stats["downloaded"] += 1

        except Exception as e:
            error_msg = str(e)
            print(f"[{idx}/{total_months}] {period_str} ✗ Failed: {error_msg}")
            print(f"Traceback: {traceback.format_exc()}")
            stats["failed"] += 1
            failed_months.append(f"{period_str}: {error_msg}")
            continue

    elapsed = time.time() - start_time
    return {**stats, "elapsed": elapsed, "failed_months": failed_months}


def main() -> int:
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(description="CLI for ingesting ERA5 climate datasets.")
    subparsers = parser.add_subparsers(title="Commands", dest="command")

    bootstrap_parser = subparsers.add_parser("bootstrap", help="Bootstrap data ingestion")
    bootstrap_parser.add_argument("--from-year", type=int, default=2020, help="Start year (default: 2020)")
    bootstrap_parser.add_argument("--from-month", type=int, default=1, help="Start month (default: 1)")
    bootstrap_parser.add_argument("--to-year", type=int, default=2025, help="End year (default: 2025)")
    bootstrap_parser.add_argument("--to-month", type=int, default=12, help="End month (default: 12)")

    args, remaining = parser.parse_known_args()

    if args.command == "bootstrap":
        result = asyncio.run(bootstrap_ingestion(
            from_year=args.from_year,
            from_month=args.from_month,
            to_year=args.to_year,
            to_month=args.to_month
        ))
        elapsed = result["elapsed"]
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        print()
        print("Bootstrap complete")
        print()
        print(f"Downloaded: {result['downloaded']}")
        print(f"Skipped:    {result['skipped']}")
        print(f"Failed:     {result['failed']}")
        if result["failed"] > 0:
            print()
            print("Failed months:")
            for fm in result["failed_months"]:
                print(f"  - {fm}")
        print()
        print(f"Elapsed: {minutes}m {seconds}s")
        return 0

    # Handle the single ingest command
    if len(remaining) == 0:
        parser.print_help()
        return 1

    provider = remaining[0]
    variable = remaining[1]
    year = int(remaining[2])
    month = int(remaining[3])

    region = None
    if len(remaining) == 8:
        region = (
            float(remaining[4]),  # west
            float(remaining[5]),  # south
            float(remaining[6]),  # east
            float(remaining[7]),  # north
        )

    try:
        response = asyncio.run(ingest_dataset(provider, variable, year, month, region))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        return 1

    if response.success:
        result = {
            "success": True,
            "s3_object_key": response.storage_key,
            "dataset_provider": response.provider,
            "variable": response.variable,
            "year": response.year,
            "month": response.month,
            "file_size": response.file_size,
            "checksum": response.checksum,
        }
    else:
        result = {
            "success": False,
            "error": response.error_message,
        }

    print(json.dumps(result, indent=2))
    return 0 if response.success else 1


if __name__ == "__main__":
    sys.exit(main())
