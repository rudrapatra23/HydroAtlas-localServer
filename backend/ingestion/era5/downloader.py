from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path
import shutil
from typing import Protocol
import uuid
import zipfile

import xarray as xr

from core.config import Settings
from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort

from ingestion.era5.checksums import sha256_file
from ingestion.era5.file_service import FileService
from ingestion.era5.hashing import month_bundle_hash
from ingestion.era5.locks import LockRegistry, lock_registry
from ingestion.era5.splitter import (
    DEFAULT_ERA5_VARIABLES,
    DatasetSplitter,
    Era5Variable,
    SplitFile,
    VARIABLE_CATEGORY,
)
from ingestion.era5.timing import (
    PHASE_ERA5_DOWNLOAD,
    PHASE_METADATA_LOOKUP,
    PHASE_S3_DOWNLOAD,
    PHASE_S3_UPLOAD,
    PhaseTimer,
    log_ensure_summary,
)
from ingestion.era5.validation import validate_year_month


class CdsClient(Protocol):
    def retrieve(self, name: str, request: dict[str, object], target: str) -> object:
        ...


@dataclass(frozen=True)
class DatasetHandle:
    """Result of :meth:`downloader."""

    local_path: Path
    storage_key: str
    checksum: str
    file_size: int
    cache_hit: bool
    timings_ms: dict[str, float]


def _category_to_cds_variable(
    variable: str,
    era5_variables: tuple[Era5Variable, ...],
) -> str:
    """Resolve the cds ``variable`` long name for a logical category."""
    for var in era5_variables:
        category = VARIABLE_CATEGORY.get(var.name, var.name)
        if category == variable:
            return var.name
    raise ValueError(
        f"unknown variable {variable!r}; expected one of "
        f"{sorted({VARIABLE_CATEGORY.get(v.name, v.name) for v in era5_variables})}"
    )


class Downloader:
    """Per-variable era5-land ingestion pipeline."""

    def __init__(
        self,
        settings: Settings,
        files: FileService,
        splitter: DatasetSplitter | None = None,
        storage_port: StoragePort | None = None,
        locks: LockRegistry = lock_registry,
        cds_client: CdsClient | None = None,
        era5_variables: tuple[Era5Variable, ...] = DEFAULT_ERA5_VARIABLES,
    ) -> None:
        self._settings = settings
        self._files = files
        self._splitter = splitter
        self._storage_port = storage_port
        self._locks = locks
        self._cds_client = cds_client
        self._logger = logging.getLogger(__name__)
        self._dataset = settings.era5_dataset
        self._era5_variables = era5_variables
        self._retry_attempts = settings.era5_retry_attempts
        self._retry_base_seconds = settings.era5_retry_base_seconds
        self._temp_dir = settings.era5_storage_root_resolved() / "tmp"
        self._s3_prefix = settings.era5_s3_prefix

    # ── Public API

    async def ensure_dataset(
        self,
        *,
        provider: str,
        variable: str,
        year: int,
        month: int,
        repository: DatasetRepository,
    ) -> DatasetHandle:
        
        validate_year_month(year, month)
        timer = PhaseTimer()
        timer.start_total()

        self._logger.debug(
            "ensure_dataset.start provider=%s variable=%s year=%s month=%s",
            provider,
            variable,
            year,
            month,
        )

        # 1. Metadata lookup (cache hit?)
        with timer.phase(PHASE_METADATA_LOOKUP):
            existing = await repository.get_by_period(year, month, provider, variable)

        if existing is not None:
            handle = await self._serve_from_cache_or_s3(
                asset=existing,
                provider=provider,
                variable=variable,
                year=year,
                month=month,
                timer=timer,
            )
            self._emit_summary(
                timer=timer,
                provider=provider,
                variable=variable,
                year=year,
                month=month,
                cache_hit=handle.cache_hit,
                source="db",
            )
            return handle

        # 2. Cache miss — take a per-variable lock to coalesce concurrent
        # first writers, then re-check the DB inside the lock.
        key = month_bundle_hash(year, month, self._dataset, (variable,))
        async with self._locks.download_lock(key):
            with timer.phase(PHASE_METADATA_LOOKUP):
                recheck = await repository.get_by_period(
                    year, month, provider, variable
                )
            if recheck is not None:
                handle = await self._serve_from_cache_or_s3(
                    asset=recheck,
                    provider=provider,
                    variable=variable,
                    year=year,
                    month=month,
                    timer=timer,
                )
                self._emit_summary(
                    timer=timer,
                    provider=provider,
                    variable=variable,
                    year=year,
                    month=month,
                    cache_hit=handle.cache_hit,
                    source="db",
                )
                return handle
            # 3. Still missing — fetch from CDS.
            handle = await self._fetch_from_era5(
                provider=provider,
                variable=variable,
                year=year,
                month=month,
                repository=repository,
                timer=timer,
            )
            self._emit_summary(
                timer=timer,
                provider=provider,
                variable=variable,
                year=year,
                month=month,
                cache_hit=False,
                source="era5",
            )
            return handle

    async def upload_to_s3_and_register(
        self,
        *,
        repository: DatasetRepository,
        variable: str,
        year: int,
        month: int,
        provider: str,
        source: Path,
    ) -> ClimateAsset:
       
        if self._storage_port is None:
            raise RuntimeError("StoragePort is not configured")

        cache_path = self._files.cache_path_for(provider, variable, year, month)
        self._files.ensure_cache_dir(provider, variable, year, month)
        await asyncio.to_thread(self._move_into_cache, source, cache_path)

        s3_key = f"{self._s3_prefix}/{variable}/{year:04d}/{month:02d}.nc"
        await asyncio.to_thread(self._storage_port.upload, s3_key, cache_path)
        checksum = await asyncio.to_thread(sha256_file, cache_path)
        file_size = cache_path.stat().st_size

        now = datetime.now(timezone.utc)
        asset = ClimateAsset(
            id=None,
            provider=provider,
            variable=variable,
            year=year,
            month=month,
            storage_key=s3_key,
            checksum=checksum,
            file_size=file_size,
            status=ClimateAssetStatus.COMPLETED,
            created_at=now,
            updated_at=now,
        )
        saved = await repository.save(asset)
        self._logger.info(
            "Uploaded+registered provider=%s variable=%s year=%s month=%s s3_key=%s",
            provider,
            variable,
            year,
            month,
            s3_key,
        )
        return saved

    async def repair_registered_asset(
        self,
        *,
        asset: ClimateAsset,
        repository: DatasetRepository,
    ) -> DatasetHandle:
        
        if self._storage_port is None:
            raise RuntimeError("StoragePort is not configured")

        cache_path = self._files.cache_path_for(
            asset.provider, asset.variable, asset.year, asset.month
        )
        self._files.ensure_cache_dir(
            asset.provider, asset.variable, asset.year, asset.month
        )
        if cache_path.exists():
            checksum = await asyncio.to_thread(sha256_file, cache_path)
            if checksum == asset.checksum:
                await asyncio.to_thread(
                    self._storage_port.upload, asset.storage_key, cache_path
                )
                return DatasetHandle(
                    local_path=cache_path,
                    storage_key=asset.storage_key,
                    checksum=checksum,
                    file_size=cache_path.stat().st_size,
                    cache_hit=True,
                    timings_ms={},
                )
            self._logger.warning(
                "Local cache checksum drift during repair for %s/%s/%04d-%02d; "
                "falling back to CDS re-fetch",
                asset.provider,
                asset.variable,
                asset.year,
                asset.month,
            )

        timer = PhaseTimer()
        timer.start_total()
        handle = await self._fetch_from_era5(
            provider=asset.provider,
            variable=asset.variable,
            year=asset.year,
            month=asset.month,
            repository=repository,
            timer=timer,
            existing_asset=asset,
        )
        self._emit_summary(
            timer=timer,
            provider=asset.provider,
            variable=asset.variable,
            year=asset.year,
            month=asset.month,
            cache_hit=False,
            source="era5",
        )
        return handle

    # ── Internal: cache / S3 path

    async def _serve_from_cache_or_s3(
        self,
        *,
        asset: ClimateAsset,
        provider: str,
        variable: str,
        year: int,
        month: int,
        timer: PhaseTimer,
    ) -> DatasetHandle:
        if self._storage_port is None:
            raise RuntimeError("StoragePort is not configured")

        cache_path = self._files.cache_path_for(provider, variable, year, month)
        self._files.ensure_cache_dir(provider, variable, year, month)

        if cache_path.exists():
            checksum = await asyncio.to_thread(sha256_file, cache_path)
            if checksum == asset.checksum:
                return DatasetHandle(
                    local_path=cache_path,
                    storage_key=asset.storage_key,
                    checksum=checksum,
                    file_size=cache_path.stat().st_size,
                    cache_hit=True,
                    timings_ms=dict(timer.elapsed_ms),
                )
            self._logger.warning(
                "Local cache checksum drift for %s/%s/%s/%s "
                "(db=%s, disk=%s); re-downloading from S3",
                provider,
                variable,
                year,
                month,
                asset.checksum,
                checksum,
            )
            try:
                cache_path.unlink()
            except FileNotFoundError:
                pass

        with timer.phase(PHASE_S3_DOWNLOAD):
            await asyncio.to_thread(
                self._storage_port.download, asset.storage_key, cache_path
            )
        checksum = await asyncio.to_thread(sha256_file, cache_path)
        if checksum != asset.checksum:
            raise RuntimeError(
                f"S3 download checksum mismatch for {asset.storage_key}: "
                f"db={asset.checksum}, downloaded={checksum}"
            )
        return DatasetHandle(
            local_path=cache_path,
            storage_key=asset.storage_key,
            checksum=checksum,
            file_size=cache_path.stat().st_size,
            cache_hit=False,
            timings_ms=dict(timer.elapsed_ms),
        )

    # ── Internal: CDS fetch path

    async def _fetch_from_era5(
        self,
        *,
        provider: str,
        variable: str,
        year: int,
        month: int,
        repository: DatasetRepository,
        timer: PhaseTimer,
        existing_asset: ClimateAsset | None = None,
    ) -> DatasetHandle:
        """Cds download + split + cache + upload + register for a single."""
        if self._splitter is None:
            raise RuntimeError("DatasetSplitter is not configured")
        if self._storage_port is None:
            raise RuntimeError("StoragePort is not configured")

        cds_variable = _category_to_cds_variable(variable, self._era5_variables)
        request = self._cds_request(cds_variable, year, month)

        bundle_path = self._files.path_for_filename(self._files.filename_for(year, month))
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        # Per-call unique temp path: two concurrent ``ensure_dataset``
        # calls for different variables on the same month must not race
        # on the temp file the CDS download writes to. The bundle_path
        # itself is still keyed by ``(year, month)`` because the splitter
        # owns it under the assumption that exactly one writer wins; we
        # let the last writer succeed, since the splitter's output is
        # only used by the caller of *this* invocation.
        temp_bundle = self._files.temp_path_for(year, month).with_name(
            f"{self._files.temp_path_for(year, month).name}.{uuid.uuid4().hex[:8]}"
        )

        self._logger.info(
            "era5.download.start provider=%s variable=%s year=%s month=%s",
            provider,
            variable,
            year,
            month,
        )
        splits: list[SplitFile] = []
        with timer.phase(PHASE_ERA5_DOWNLOAD):
            await self._download_with_retry(request, temp_bundle)
            await asyncio.to_thread(self._ensure_normalized, temp_bundle)
            temp_bundle.replace(bundle_path)
            splits = await asyncio.to_thread(
                self._splitter.split, bundle_path, year, month, self._temp_dir
            )

        matching = [sf for sf in splits if sf.category == variable]
        if not matching:
            extras = [sf.category for sf in splits]
            self._cleanup_bundle_and_splits(bundle_path, splits)
            raise RuntimeError(
                f"splitter produced no file for variable={variable!r}; "
                f"got categories {extras}"
            )
        split_file = matching[0]

        cache_path = self._files.cache_path_for(provider, variable, year, month)
        self._files.ensure_cache_dir(provider, variable, year, month)
        await asyncio.to_thread(self._move_into_cache, split_file.path, cache_path)

        s3_key = f"{self._s3_prefix}/{variable}/{year:04d}/{month:02d}.nc"
        with timer.phase(PHASE_S3_UPLOAD):
            await asyncio.to_thread(self._storage_port.upload, s3_key, cache_path)
        checksum = await asyncio.to_thread(sha256_file, cache_path)
        file_size = cache_path.stat().st_size

        now = datetime.now(timezone.utc)
        asset = ClimateAsset(
            id=existing_asset.id if existing_asset is not None else None,
            provider=provider,
            variable=variable,
            year=year,
            month=month,
            storage_key=s3_key,
            checksum=checksum,
            file_size=file_size,
            status=ClimateAssetStatus.COMPLETED,
            created_at=existing_asset.created_at if existing_asset is not None else now,
            updated_at=now,
        )
        await repository.save(asset)

        self._cleanup_bundle_and_splits(bundle_path, splits, keep=cache_path)

        self._logger.info(
            "era5.download.complete provider=%s variable=%s year=%s month=%s "
            "s3_key=%s bytes=%d",
            provider,
            variable,
            year,
            month,
            s3_key,
            file_size,
        )

        return DatasetHandle(
            local_path=cache_path,
            storage_key=s3_key,
            checksum=checksum,
            file_size=file_size,
            cache_hit=False,
            timings_ms=dict(timer.elapsed_ms),
        )

    # ── Internal: helpers

    def _cds_request(
        self,
        cds_variable: str,
        year: int,
        month: int,
    ) -> dict[str, object]:
        return {
            "product_type": ["monthly_averaged_reanalysis"],
            "variable": [cds_variable],
            "year": [f"{year:04d}"],
            "month": [f"{month:02d}"],
            "time": ["00:00"],
            "data_format": "netcdf",
        }
        
        

    async def _download_with_retry(
        self,
        request: dict[str, object],
        target: Path,
    ) -> None:
        """Run the blocking cds request with the configured retry policy."""
        last_exc: Exception | None = None
        for attempt in range(1, self._retry_attempts + 1):
            try:
                await asyncio.to_thread(self._retrieve, request, target)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                try:
                    if target.exists():
                        target.unlink()
                except OSError:
                    pass
                if attempt >= self._retry_attempts:
                    self._logger.exception(
                        "CDS download failed after %d attempts: %s",
                        self._retry_attempts,
                        exc,
                    )
                    raise
                self._logger.warning(
                    "CDS download retry %d/%d: %s",
                    attempt + 1,
                    self._retry_attempts,
                    exc,
                )
                await asyncio.sleep(self._retry_base_seconds * (2 ** (attempt - 1)))
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("CDS retry loop exited unexpectedly")

    def _retrieve(self, request: dict[str, object], target: Path) -> None:
        client = self._cds_client or self._create_cds_client()
        client.retrieve(self._dataset, request, str(target))

    def _create_cds_client(self) -> CdsClient:
        try:
            import cdsapi
        except ImportError as exc:
            raise RuntimeError("cdsapi is required for ERA5 downloads") from exc
        if self._settings.cdsapi_url and self._settings.cdsapi_key:
            return cdsapi.Client(
                url=self._settings.cdsapi_url, key=self._settings.cdsapi_key
            )
        return cdsapi.Client()

    def _move_into_cache(self, source: Path, cache_path: Path) -> None:
        """Atomically move ``source`` to ``cache_path`` (overwriting)."""
        if cache_path.exists():
            cache_path.unlink()
        shutil.move(str(source), str(cache_path))

    def _ensure_normalized(self, target: Path) -> None:
        """Run the single normalize-then-validate pipeline on ``target``."""
        if not zipfile.is_zipfile(target):
            self._validate_artifact(target)
            return

        self._logger.info("Detected ZIP at %s; extracting inner .nc", target)
        with zipfile.ZipFile(target) as zf:
            nc_members = [
                name
                for name in zf.namelist()
                if name.endswith(".nc") and not name.endswith("/")
            ]
            if len(nc_members) == 0:
                raise RuntimeError(
                    f"CDS ZIP at {target} contains no .nc members "
                    f"(entries: {zf.namelist()!r})"
                )
            if len(nc_members) > 1:
                raise RuntimeError(
                    f"CDS ZIP at {target} contains "
                    f"{len(nc_members)} .nc members ({nc_members!r}); "
                    f"expected exactly 1"
                )
            inner_name = nc_members[0]
            with zf.open(inner_name) as src:
                inner_bytes = src.read()
        target.write_bytes(inner_bytes)
        self._logger.info(
            "Extracted %s into %s (%d bytes)",
            inner_name,
            target,
            target.stat().st_size,
        )
        self._validate_artifact(target)

    def _validate_artifact(self, path: Path) -> None:
        if not path.exists():
            raise RuntimeError("downloaded file is missing")
        if path.stat().st_size <= 0:
            raise RuntimeError("downloaded file is empty")

    def _cleanup_bundle_and_splits(
        self,
        bundle_path: Path,
        splits: list[SplitFile],
        keep: Path | None = None,
    ) -> None:
        """Best-effort cleanup of intermediate netcdf artifacts."""
        try:
            if bundle_path.exists():
                bundle_path.unlink()
        except OSError as exc:
            self._logger.warning(
                "Failed to remove bundle %s during cleanup: %s", bundle_path, exc
            )
        for sf in splits:
            if sf.path == keep:
                continue
            try:
                if sf.path.exists():
                    sf.path.unlink()
            except OSError as exc:
                self._logger.warning(
                    "Failed to remove split %s during cleanup: %s", sf.path, exc
                )

    def _emit_summary(
        self,
        *,
        timer: PhaseTimer,
        provider: str,
        variable: str,
        year: int,
        month: int,
        cache_hit: bool,
        source: str,
    ) -> None:
        log_ensure_summary(
            self._logger,
            provider=provider,
            variable=variable,
            year=year,
            month=month,
            phases=timer.elapsed_ms,
            cache_hit=cache_hit,
            source=source,
            total_ms=timer.total_ms(),
        )


__all__ = ["CdsClient", "DatasetHandle", "Downloader"]
