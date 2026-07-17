from __future__ import annotations

from typing import Dict, Any

import numpy as np

from hydroatlas.raster_clip import ClippedRasterResult


def compute_stats(result: ClippedRasterResult) -> Dict[str, Any]:
    """Compute basic stats for the valid pixels in a clipped district."""
    vals = result.valid_data
    # If fractional overlaps provided, compute weighted stats
    overlaps = getattr(result, "overlap_fractions", None)
    if overlaps is not None:
        # flatten overlaps aligned with data mask
        mask = np.asarray(result.data.mask)
        frac = overlaps[~mask]
        vals = result.valid_data
        weighted_sum = float(np.sum(vals * frac)) if len(vals) else 0.0
        total_effective = float(np.sum(frac))
    else:
        weighted_sum = float(np.sum(vals)) if len(vals) else 0.0
        total_effective = float(len(vals))

    if len(vals) == 0:
        return {
            "district_id": result.district_id,
            "raster": str(result.raster_path),
            "band": result.band,
            "valid_pixels": 0,
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
            "sum": None,
            "median": None,
            "p25": None,
            "p75": None,
        }

    return {
        "district_id": result.district_id,
        "raster": str(result.raster_path),
        "band": result.band,
        "valid_pixels": len(vals),
        "mean": float(np.mean(vals)),
        "std": float(np.std(vals)),
        "min": float(np.min(vals)),
        "max": float(np.max(vals)),
        "sum": float(np.sum(vals)),
        # fractional/area-weighted metrics
        "overlap_weighted_sum": weighted_sum,
        "effective_cell_count": total_effective,
        "overlap_weighted_mean": (weighted_sum / total_effective) if total_effective > 0 else None,
        "median": float(np.median(vals)),
        "p25": float(np.percentile(vals, 25)),
        "p75": float(np.percentile(vals, 75)),
    }


def percentile(result: ClippedRasterResult, q: float) -> float:
    """Return the requested percentile for the valid pixels."""
    vals = result.valid_data
    if len(vals) == 0:
        return float("nan")
    return float(np.percentile(vals, q))
