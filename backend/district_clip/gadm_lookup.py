"""Small wrapper around the shared gadm boundary loader."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from shapely.geometry.base import BaseGeometry

# The production GADM loader is shared across the whole backend.
# Importing it here keeps a single canonical copy of the GADM
# GeoPackage in memory.
from infrastructure.geospatial.boundary_loader import get_adm2


class DistrictNotFoundError(KeyError):
    """Raised when a district id is missing from the gadm layer."""

    def __init__(self, gid: str, sample_keys: list[str] | None = None):
        self.gid = gid
        self.sample_keys = sample_keys or []
        msg = f"District '{gid}' not found in GADM ADM_2 layer."
        if self.sample_keys:
            msg += f" Sample known GIDs (first {len(self.sample_keys)}): {self.sample_keys}"
        super().__init__(msg)


@dataclass(frozen=True)
class DistrictMetadata:
    """Simple district labels used in the clip response."""

    gid_2: str
    gid_1: str
    name_2: str
    name_1: str


def lookup_district(gid: str) -> Tuple[BaseGeometry, DistrictMetadata]:
    """Resolve a district id to its geometry and metadata."""
    if not gid or not isinstance(gid, str):
        raise DistrictNotFoundError(str(gid))

    adm2 = get_adm2()
    match = adm2[adm2["GID_2"] == gid]
    if match.empty:
        sample = list(adm2["GID_2"].head(5).astype(str))
        raise DistrictNotFoundError(gid, sample_keys=sample)

    row = match.iloc[0]
    geometry = row.geometry
    if geometry is None or geometry.is_empty:
        raise DistrictNotFoundError(gid, sample_keys=[])

    metadata = DistrictMetadata(
        gid_2=str(row["GID_2"]),
        gid_1=str(row["GID_1"]),
        name_2=str(row.get("NAME_2", "")),
        name_1=str(row.get("NAME_1", "")),
    )
    return geometry, metadata
