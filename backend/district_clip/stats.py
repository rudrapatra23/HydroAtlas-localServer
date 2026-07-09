"""
stats.py
========
Compute district-level raster statistics from a ``ClippedRasterResult``.

All statistics operate exclusively on pixels that passed the exact-polygon
mask – i.e. pixels inside the district boundary.  Pixels in the bounding-box
margin (outside the district) are never included.

Public API
----------
compute_stats(result)   -> dict
percentile(result, q)   -> float
"""

from __future__ import annotations

from typing import Dict, Any

import numpy as np

from hydroatlas.raster_clip import ClippedRasterResult


def compute_stats(result: ClippedRasterResult) -> Dict[str, Any]:
    """
    Compute common descriptive statistics over valid district pixels.

    Parameters
    ----------
    result :
        A :class:`~hydroatlas.raster_clip.ClippedRasterResult` returned by
        :meth:`~hydroatlas.raster_clip.DistrictRasterClipper.clip`.

    Returns
    -------
    dict with keys:
        ``district_id``, ``raster``, ``band``, ``valid_pixels``,
        ``mean``, ``std``, ``min``, ``max``, ``sum``, ``median``,
        ``p25``, ``p75``.
    """
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
    """
    Return the *q*-th percentile of valid district pixels.

    Parameters
    ----------
    result :
        Clipped raster result.
    q :
        Percentile in the range ``[0, 100]``.

    Returns
    -------
    float
        The *q*-th percentile, or ``float('nan')`` if no valid pixels exist.
    """
    vals = result.valid_data
    if len(vals) == 0:
        return float("nan")
    return float(np.percentile(vals, q))
