from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import logging
from pathlib import Path
import tempfile  
import time
import tracemalloc
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.raster_cache import OpenRasterHandle, RasterCache
from application.raster_computation import RasterComputation
from domain.entities.climate_asset import ClimateAsset
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort
from infrastructure.geospatial.boundary_loader import get_adm2
from infrastructure.repositories.postgres_dataset_repository import (
    SqlAlchemyDatasetRepository,
)
from infrastructure.repositories.postgres_district_monthly_statistics_repository import (
    DistrictMonthlyStatisticsRow,
    SqlAlchemyDistrictMonthlyStatisticsRepository,
)


logger = logging.getLogger("ingestion.era5.precompute")


@dataclass(frozen=True)
class PrecomputeTimings:
    """Wall-time and memory measurements for one precompute invocation."""

    s3_read_seconds: float
    dataset_open_seconds: float
    clipping_total_seconds: float
    db_upsert_seconds: float
    total_seconds: float
    peak_memory_mb: float


@dataclass(frozen=True)
class PrecomputeResult:
    """Outcome of precomputing a single ``(provider, variable, year, month)``."""

    asset: ClimateAsset
    rows_upserted: int
    districts_processed: int
    timings: PrecomputeTimings


class PrecomputeService:
    """This service handles the heavy lifting of pre-calculating statistics."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: StoragePort,
        raster_computation: RasterComputation,
        raster_cache: RasterCache | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._raster_computation = raster_computation
        # Module-level lock and lease registries are singletons; the
        # precompute service reuses the same cache as the runtime path.
        self._raster_cache = raster_cache or RasterCache()

    async def precompute_one(
        self,
        *,
        provider: str,
        variable: str,
        year: int,
        month: int,
    ) -> PrecomputeResult:
        """Resolve the asset, read the raster once, clip every district,."""
        tracemalloc.start()
        t_total_start = time.perf_counter()

        # Resolve the source asset through PostgreSQL using a short-lived
        # session; the long clipping loop below would otherwise leave the
        # connection idle.
        async with self._session_factory() as lookup_session:
            lookup_repo = SqlAlchemyDatasetRepository(lookup_session)
            asset = await lookup_repo.get_by_period(
                year=year, month=month, provider=provider, variable=variable
            )
        if asset is None:
            tracemalloc.stop()
            raise ValueError(
                f"No climate_assets row for {provider}/{variable}/{year:04d}-{month:02d}; "
                f"ingest this period first via `python -m ingestion.era5.cli "
                f"download {year} {month} --variable {variable}`."
            )

        # 2. Read the S3 raster once via the shared cache module.
        # ``acquire`` either returns a cache hit (zero S3 cost) or
        # performs a single S3 download, validating the SHA-256
        # against ``asset.checksum``. ``s3_read_seconds`` is set from
        # the lease so a cache hit reports 0.0 here, which is correct.
        #
        # We wrap the lease + opened dataset in an ``OpenRasterHandle``
        # so the dataset file handle and the eviction-protection
        # refcount are released together. We deliberately close the
        # dataset BEFORE the upsert (memory optimisation) — xarray's
        # ``Dataset.close()`` is idempotent so the handle's late
        # ``close()`` in the finally block is a safe no-op on the
        # already-closed dataset.
        acquired = await self._raster_cache.acquire(asset, self._storage)
        s3_read_seconds = acquired.download_seconds
        cache_path = acquired.path
        handle: OpenRasterHandle | None = None
        try:
            # 3. Open the dataset once, assign CRS, select the variable.
            t_open_start = time.perf_counter()
            rds = await self._raster_cache.open_dataset(acquired, asset=asset)
            rds = rds.rio.write_crs("EPSG:4326")
            handle = OpenRasterHandle(
                dataset=rds, path=cache_path, lease=acquired,
            )
            data = self._raster_computation._select_raster_variable(rds, variable)
            dataset_open_seconds = time.perf_counter() - t_open_start

            # 4. Process every GADM district with the existing stats helper.
            #
            # The existing on-demand path (``compute_for_state_range`` in
            # ``raster_computation.py:480–487``) wraps the per-district
            # clip in ``try/except Exception`` so districts whose bounds
            # do not overlap the raster (e.g. Andaman & Nicobar islands
            # at the edge of the ERA5 grid, CRS quirks) are logged and
            # skipped. The precompute path must do the same so the
            # resulting rows match what the routers would compute.
            t_clip_start = time.perf_counter()
            adm2 = get_adm2()
            now = datetime.now(timezone.utc)
            rows: list[DistrictMonthlyStatisticsRow] = []
            skipped: list[tuple[str, str]] = []
            for idx in adm2.index:
                district_gid_2 = str(adm2.at[idx, "GID_2"])
                district_gid_1 = str(adm2.at[idx, "GID_1"])
                district_geometry = adm2.loc[[idx]]
                try:
                    clip = self._raster_computation._compute_stats_for_geometry(
                        data, district_geometry
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "precompute.skip district=%s state=%s reason=%s",
                        district_gid_2,
                        district_gid_1,
                        type(exc).__name__,
                    )
                    skipped.append((district_gid_2, type(exc).__name__))
                    continue
                rows.append(
                    DistrictMonthlyStatisticsRow(
                        provider=provider,
                        variable=variable,
                        gid_2=district_gid_2,
                        gid_1=district_gid_1,
                        year=year,
                        month=month,
                        pixel_count=clip.pixel_count,
                        valid_pixel_count=clip.valid_pixel_count,
                        valid_pixel_pct=Decimal(
                            f"{clip.valid_pixel_percentage:.2f}"
                        ),
                        mean=clip.mean,
                        minimum=clip.minimum,
                        maximum=clip.maximum,
                        source_asset_id=asset.id,
                        bbox=tuple(clip.bounds),
                    )
                )
            clipping_total_seconds = time.perf_counter() - t_clip_start
            districts_processed = len(rows)

            # Close the xarray handle before the upsert so peak memory
            # does not double-count the raster during the SQL round-trip.
            # ``handle.close()`` later in the finally block is still
            # safe because xarray ``Dataset.close()`` is idempotent.
            try:
                rds.close()
            except Exception:  # noqa: BLE001
                pass
        finally:
            # ``OpenRasterHandle.close()`` releases the cache lease
            # (making the file eviction-eligible and cleaning up the
            # tempfile in ``max_bytes == 0`` mode) and idempotently
            # re-closes the already-closed dataset. This is the
            # atomic release path for runtime resource ownership.
            if handle is not None:
                await handle.aclose()

        # 5. Bulk upsert in batches so a long-running precompute does
        # not lose all its work if the process is killed mid-loop.
        # Each batch opens a fresh asyncpg session (via
        # ``session_factory``) so the connection is warm for the
        # duration of one statement instead of sitting idle through
        # the clipping loop. The batch size is a balance between
        # commit overhead and the cost of re-running a batch after
        # a kill — 100 keeps each transaction under a few hundred ms.
        t_db_start = time.perf_counter()
        BATCH_SIZE = 100
        rows_upserted = 0
        for batch_start in range(0, len(rows), BATCH_SIZE):
            batch = rows[batch_start : batch_start + BATCH_SIZE]
            async with self._session_factory() as batch_session:
                batch_repo = SqlAlchemyDistrictMonthlyStatisticsRepository(batch_session)
                rows_upserted += await batch_repo.bulk_upsert(batch)
        db_upsert_seconds = time.perf_counter() - t_db_start

        total_seconds = time.perf_counter() - t_total_start
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_memory_mb = peak / (1024 * 1024)
        # ``current`` is intentionally unused; the snapshot pair is what
        # Python's tracemalloc gives us. Suppress the linter warning.
        del current

        timings = PrecomputeTimings(
            s3_read_seconds=s3_read_seconds,
            dataset_open_seconds=dataset_open_seconds,
            clipping_total_seconds=clipping_total_seconds,
            db_upsert_seconds=db_upsert_seconds,
            total_seconds=total_seconds,
            peak_memory_mb=peak_memory_mb,
        )
        logger.info(
            "precompute.complete provider=%s variable=%s year=%s month=%s "
            "districts=%d rows_upserted=%d s3_read=%.3fs dataset_open=%.3fs "
            "clipping=%.3fs db_upsert=%.3fs total=%.3fs peak_mem=%.1fMiB",
            provider,
            variable,
            year,
            month,
            districts_processed,
            rows_upserted,
            s3_read_seconds,
            dataset_open_seconds,
            clipping_total_seconds,
            db_upsert_seconds,
            total_seconds,
            peak_memory_mb,
        )
        return PrecomputeResult(
            asset=asset,
            rows_upserted=rows_upserted,
            districts_processed=districts_processed,
            timings=timings,
        )


# Silence the linter about unused import; ``Sequence`` is exported so
# callers can type-hint against the row list.
__all__ = ["PrecomputeResult", "PrecomputeService", "PrecomputeTimings", "Sequence"]
