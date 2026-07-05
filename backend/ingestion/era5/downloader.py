"""ERA5-Land ingestion: PostgreSQL-as-source-of-truth per-variable flow.

The refactor described in ``.kimchi/docs/era5-pg-source-of-truth.md`` replaced
the bundle-level :class:`~ingestion.era5.manifest_manager.ManifestManager`
with a per-variable lookup against the ``climate_assets`` PostgreSQL table.
The local filesystem is now a transient per-variable cache at
``storage_root/cache/{provider}/{variable}/{YYYY}/{MM}.nc``; deleting it
never triggers an ERA5 download if metadata + S3 still exist.

Public surface:

- :meth:`Downloader.ensure_dataset` — the single entry point. Async.
  Looks up PostgreSQL, then S3, then CDS in that order.
- :meth:`Downloader.upload_to_s3_and_register` — uploads an already-normalized
  per-variable NetCDF to S3 and inserts the matching ``ClimateAssetModel``
  row. Used by :meth:`Downloader.ensure_dataset` internally and exposed for
  callers that have already produced the per-variable file some other way
  (e.g. a one-shot bootstrap from a local mirror).

The legacy ``ensure_downloaded`` (sync CDS fetch) and ``publish``
(split + upload + register) methods are removed; the CLI now iterates
``ensure_dataset`` per ``(variable, year, month)``.

The ``manifest.json`` file left over from the old code is harmless: nothing
reads it. A future operator may delete it manually.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import logging
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
    """Result of :meth:`Downloader.ensure_dataset`.

    Carries the local cache path (always set) plus the canonical S3
    metadata (``storage_key``, ``checksum``, ``file_size``) so callers do
    not have to re-fetch them. ``cache_hit`` distinguishes "file was on
    local disk" (``True``) from "we just downloaded it from S3 or CDS"
    (``False``).
    """

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
    """Resolve the CDS ``variable`` long name for a logical category
    (e.g. ``precipitation`` -> ``total_precipitation``).

    Raises :class:`ValueError` if the category is not in the configured
    variable set — surfaces a programming error early rather than sending
    a malformed request to CDS.
    """
    for var in era5_variables:
        category = VARIABLE_CATEGORY.get(var.name, var.name)
        if category == variable:
            return var.name
    raise ValueError(
        f"unknown variable {variable!r}; expected one of "
        f"{sorted({VARIABLE_CATEGORY.get(v.name, v.name) for v in era5_variables})}"
    )


class Downloader:
    """Per-variable ERA5-Land ingestion pipeline.

    :meth:`ensure_dataset` is the single entry point. It is async;
    blocking calls (CDS, S3, disk I/O) are wrapped in
    :func:`asyncio.to_thread` so the event loop stays responsive while
    a long CDS download is in flight.
    """

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
        """Resolve a single ``(provider, variable, year, month)`` dataset.

        Lookup order:
          1. PostgreSQL (``repository.get_by_period``).
          2. S3 (download into local cache if the row exists but the
             cache file is missing or has drifted).
          3. CDS (download + split + upload + register).

        Always emits a structured summary log line on completion. The
        lock key is per-variable, so concurrent requests for *different*
        variables on the same month do not block each other; concurrent
        requests for the *same* variable coalesce via a DB re-check
        inside the lock.
        """
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
        """Upload an already-normalized per-variable NetCDF to S3 and
        insert a :class:`ClimateAsset` row.

        ``source`` is moved into the per-variable cache directory first
        so the S3 upload and the local cache stay in sync. The local
        cache is purely a downloaded-from-S3 mirror; whatever ends up in
        S3 is the source of truth.
        """
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
        """Resolve a DB-known asset to a local cache path.

        - If the file is already in the per-variable cache and its
          checksum matches the DB: ``cache_hit=True``.
        - If the file is missing or has drifted: re-download from S3,
          ``cache_hit=False``. Checksum drift is logged at WARNING and
          triggers the re-download — silent local corruption should
          never be served.
        """
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
    ) -> DatasetHandle:
        """CDS download + split + cache + upload + register for a single
        ``(provider, variable, year, month)``.

        The CDS request asks for *only* the variable the caller asked
        for; the splitter then produces one per-variable file. Sibling
        variables for the same month — if also missing — are picked up
        by their own concurrent ``ensure_dataset`` calls; we do not try
        to batch them here because the per-variable lock makes that a
        race anyway.
        """
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
            "product_type": "monthly_averaged_reanalysis",
            "variable": [cds_variable],
            "year": f"{year:04d}",
            "month": f"{month:02d}",
            "time": "00:00",
            "format": "netcdf",
        }

    async def _download_with_retry(
        self,
        request: dict[str, object],
        target: Path,
    ) -> None:
        """Run the blocking CDS request with the configured retry policy.

        CDS responses come back as ZIP-wrapped NetCDFs. The retry loop
        deletes the partial ``target`` between attempts; the caller
        normalizes the final artifact before moving it into place.
        """
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
        """Atomically move ``source`` to ``cache_path`` (overwriting).

        ``shutil.move`` falls back to copy+remove when crossing
        filesystems; since both ``source`` (the splitter's temp output)
        and ``cache_path`` live under ``storage_root``, the fast rename
        path is the common case.
        """
        if cache_path.exists():
            cache_path.unlink()
        shutil.move(str(source), str(cache_path))

    def _ensure_normalized(self, target: Path) -> None:
        """Run the single normalize-then-validate pipeline on ``target``.

        If ``target`` is a CDS ZIP, extract the inner ``.nc`` member
        in place. Idempotent: a raw NetCDF is a no-op (still validated
        so an empty or zero-byte artifact is caught here too).
        """
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
        """Best-effort cleanup of intermediate NetCDF artifacts.

        Failures here are logged but never raised — they cannot undo the
        upload + register we already performed.
        """
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
