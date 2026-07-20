"""Compare the precomputed rows against the on-demand endpoint for 5."""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, select  # noqa: E402

from application.raster_computation import RasterComputation  # noqa: E402
from infrastructure.db.session import async_session_maker  # noqa: E402
from infrastructure.db.district_monthly_statistics_model import (  # noqa: E402
    DistrictMonthlyStatisticsModel,
)
from infrastructure.geospatial.boundary_loader import get_adm2  # noqa: E402
from infrastructure.repositories.postgres_dataset_repository import (  # noqa: E402
    PostgresDatasetRepository,
)
from infrastructure.repositories.postgres_district_monthly_statistics_repository import (  # noqa: E402
    PostgresDistrictMonthlyStatisticsRepository,
)
from infrastructure.storage.s3_storage_adapter import S3StorageAdapter  # noqa: E402


PROVIDER = "era5-land"
VARIABLE = "precipitation"
YEAR = 2025
MONTH = 7


async def pick_district_gids() -> list[tuple[str, str]]:
    """Return 5 representative ``(gid_2, label)`` pairs."""
    from infrastructure.db.session import async_session_maker
    from sqlalchemy import select
    from infrastructure.db.district_monthly_statistics_model import (
        DistrictMonthlyStatisticsModel,
    )

    adm2 = get_adm2()
    gid_to_name = dict(zip(adm2["GID_2"].tolist(), adm2["NAME_2"].tolist()))

    # Mumbai Suburban by name (the user-requested representative).
    mumbai_row = adm2[adm2["NAME_2"] == "Mumbai Suburban"]
    if mumbai_row.empty:
        raise SystemExit("GADM has no district named 'Mumbai Suburban'")
    mumbai_gid = str(mumbai_row.iloc[0]["GID_2"])

    # Four more from the precomputed table by pixel-count spread.
    async with async_session_maker() as session:
        stmt = (
            select(
                DistrictMonthlyStatisticsModel.gid_2,
                DistrictMonthlyStatisticsModel.valid_pixel_count,
            )
            .where(
                DistrictMonthlyStatisticsModel.provider == PROVIDER,
                DistrictMonthlyStatisticsModel.variable == VARIABLE,
                DistrictMonthlyStatisticsModel.year == YEAR,
                DistrictMonthlyStatisticsModel.month == MONTH,
            )
            .order_by(DistrictMonthlyStatisticsModel.valid_pixel_count.desc())
        )
        result = await session.execute(stmt)
        rows = [(str(r[0]), int(r[1])) for r in result.all()]

    if len(rows) < 4:
        raise SystemExit(
            f"Precomputed table has only {len(rows)} rows; cannot pick 4 extras"
        )
    # Largest, smallest (excluding Mumbai Suburban), and two mid-range.
    largest = rows[0]
    smallest_non_mumbai = next(
        (gid, pixels) for gid, pixels in rows if gid != mumbai_gid
    )
    mid_index = len(rows) // 2
    mid1 = next(
        (gid, pixels)
        for gid, pixels in rows[mid_index - 1 :]
        if gid != mumbai_gid
    )
    mid2 = next(
        (gid, pixels)
        for gid, pixels in rows[mid_index :]
        if gid != mumbai_gid
    )

    gids = [
        (mumbai_gid, "Mumbai Suburban"),
        (largest[0], f"largest ({gid_to_name.get(largest[0], '?')}, {largest[1]} px)"),
        (smallest_non_mumbai[0], f"smallest ({gid_to_name.get(smallest_non_mumbai[0], '?')}, {smallest_non_mumbai[1]} px)"),
        (mid1[0], f"upper-mid ({gid_to_name.get(mid1[0], '?')}, {mid1[1]} px)"),
        (mid2[0], f"lower-mid ({gid_to_name.get(mid2[0], '?')}, {mid2[1]} px)"),
    ]
    return gids


async def _load_precomputed(
    gids: list[str],
) -> dict[str, tuple[float, float, float, int]]:
    """Map ``gid_2 -> (mean, minimum, maximum, valid_pixel_count)``."""
    async with async_session_maker() as session:
        repo = PostgresDistrictMonthlyStatisticsRepository(session)
        out: dict[str, tuple[float, float, float, int]] = {}
        for gid in gids:
            row = await repo.get_for_district(
                provider=PROVIDER,
                variable=VARIABLE,
                gid_2=gid,
                year=YEAR,
                month=MONTH,
            )
            if row is None:
                continue
            out[gid] = (
                row.mean,
                row.minimum,
                row.maximum,
                row.valid_pixel_count,
            )
    return out


async def _run_on_demand(
    gids: list[str],
) -> dict[str, tuple[float, float, float, int]]:
    """Drive :meth:`rastercomputation."""
    storage = S3StorageAdapter()
    async with async_session_maker() as session:
        repository = PostgresDatasetRepository(session)
        rc = RasterComputation(repository, storage)
        out: dict[str, tuple[float, float, float, int]] = {}
        for gid in gids:
            clip = await rc.compute_for_district(
                district_gid=gid,
                provider=PROVIDER,
                variable=VARIABLE,
                year=YEAR,
                month=MONTH,
            )
            out[gid] = (
                clip.mean,
                clip.minimum,
                clip.maximum,
                clip.valid_pixel_count,
            )
    return out


async def main() -> int:
    gids_with_labels = await pick_district_gids()
    gids = [g for g, _ in gids_with_labels]
    label_of = dict(gids_with_labels)

    precomputed = await _load_precomputed(gids)
    on_demand = await _run_on_demand(gids)

    print(
        f"Comparison: precomputed district_monthly_statistics vs on-demand "
        f"RasterComputation.compute_for_district for {PROVIDER}/{VARIABLE}/"
        f"{YEAR:04d}-{MONTH:02d}"
    )
    print()
    header = (
        f"  {'district':<32s} {'mean Δ':>14s} {'min Δ':>14s} {'max Δ':>14s} "
        f"{'precomputed (mean/min/max)':>40s}  {'on-demand (mean/min/max)':>40s}"
    )
    print(header)
    print(f"  {'-' * 32} {'-' * 14} {'-' * 14} {'-' * 14} {'-' * 40}  {'-' * 40}")

    worst = 0.0
    for gid in gids:
        if gid not in precomputed:
            print(f"  {label_of[gid] + ' (' + gid + ')':<32s}  NO PRECOMPUTED ROW")
            continue
        if gid not in on_demand:
            print(f"  {label_of[gid] + ' (' + gid + ')':<32s}  NO ON-DEMAND ROW")
            continue
        p_mean, p_min, p_max, p_pixels = precomputed[gid]
        o_mean, o_min, o_max, o_pixels = on_demand[gid]
        d_mean = abs(p_mean - o_mean)
        d_min = abs(p_min - o_min)
        d_max = abs(p_max - o_max)
        worst = max(worst, d_mean, d_min, d_max)
        if d_pixels := abs(p_pixels - o_pixels):
            print(
                f"  NOTE: pixel_count mismatch gid={gid} "
                f"pre={p_pixels} on_demand={o_pixels}"
            )
        print(
            f"  {(label_of[gid] + ' (' + gid + ')'):<32s} "
            f"{d_mean:>14.10f} {d_min:>14.10f} {d_max:>14.10f} "
            f"{f'{p_mean:.6f} / {p_min:.6f} / {p_max:.6f}':>40s}  "
            f"{f'{o_mean:.6f} / {o_min:.6f} / {o_max:.6f}':>40s}"
        )
    print()
    print(f"Worst absolute difference across the 5 districts: {worst:.10f}")
    if worst == 0.0:
        print("RESULT: byte-identical (or numerically indistinguishable)")
    else:
        print("RESULT: small floating-point drift is expected")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
