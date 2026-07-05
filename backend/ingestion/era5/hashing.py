from __future__ import annotations

import hashlib
import json


def month_bundle_hash(
    year: int,
    month: int,
    dataset: str,
    variables: tuple[str, ...],
) -> str:
    payload = {
        "dataset": dataset,
        "year": year,
        "month": month,
        "variables": sorted(variables),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def soil_moisture_hash(year: int, month: int, variables: tuple[str, ...]) -> str:
    return month_bundle_hash(year, month, "era5-land-monthly-soil-moisture", variables)
