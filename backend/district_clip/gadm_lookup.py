"""
gadm_lookup.py
==============
Thin shim over the existing ``infrastructure.geospatial.boundary_loader``
that resolves a district's exact GADM geometry (EPSG:4326) and its
metadata for the district clipping pipeline.

Why a shim and not the prototype's own ``gadm_loader.py``?
----------------------------------------------------------
The HydroAtlas backend already loads ``gadm41_IND.gpkg`` via the
production-grade :func:`infrastructure.geospatial.boundary_loader.get_adm2`
helper, which is module-level cached and shared with the existing
``/boundaries`` and ``/districts/{id}/statistics`` routes. Re-using the
production loader guarantees:

  * Single canonical GADM file is opened (no duplicate open of a
    49 MB GeoPackage).
  * The district geometry returned here is byte-for-byte the same
    geometry used by every other HydroAtlas endpoint, so the clipped
    raster cells align exactly with the existing district choropleth.
  * CRS handling stays consistent: GADM ADM_2 always emits
    EPSG:4326, the same CRS as the ERA5 NetCDF.

Public API
----------
``lookup_district(gid)``
    Return ``(shapely_geometry, DistrictMetadata)`` for the given
    ``GID_2`` (e.g. ``"IND.16.13_1"`` for Davanagere).

``DistrictNotFoundError``
    Raised when the GID does not exist in the loaded ADM_2 layer.

``DistrictMetadata``
    Light dataclass bundling NAME_2 / NAME_1 / GID_1 / GID_2 so the
    orchestrator can attach human-readable labels to the response
    without coupling to ``geopandas``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from shapely.geometry.base import BaseGeometry

# The production GADM loader is shared across the whole backend.
# Importing it here keeps a single canonical copy of the GADM
# GeoPackage in memory.
from infrastructure.geospatial.boundary_loader import get_adm2


class DistrictNotFoundError(KeyError):
    """Raised when a ``GID_2`` does not exist in the loaded ADM_2 layer.

    Subclasses ``KeyError`` so callers that already handle ``KeyError``
    continue to work; carries a clear ``str(exc)`` payload so HTTP
    routers can map it to a 404.
    """

    def __init__(self, gid: str, sample_keys: list[str] | None = None):
        self.gid = gid
        self.sample_keys = sample_keys or []
        msg = f"District '{gid}' not found in GADM ADM_2 layer."
        if self.sample_keys:
            msg += f" Sample known GIDs (first {len(self.sample_keys)}): {self.sample_keys}"
        super().__init__(msg)


@dataclass(frozen=True)
class DistrictMetadata:
    """Human-readable labels for a district, sourced from GADM ADM_2.

    Attributes
    ----------
    gid_2 : str
        District-level GADM identifier (e.g. ``"IND.16.13_1"``).
    gid_1 : str
        State-level GADM identifier (e.g. ``"IND.16.13"``).
    name_2 : str
        District name (e.g. ``"Davanagere"``).
    name_1 : str
        State name (e.g. ``"Karnataka"``).
    """

    gid_2: str
    gid_1: str
    name_2: str
    name_1: str


def lookup_district(gid: str) -> Tuple[BaseGeometry, DistrictMetadata]:
    """Resolve a GADM ``GID_2`` to its exact geometry and metadata.

    The returned ``shapely`` geometry is guaranteed to be in
    ``EPSG:4326`` — the same coordinate system as the ERA5 NetCDF and
    the same CRS used by every other HydroAtlas endpoint that reads
    the GADM boundaries.

    Parameters
    ----------
    gid :
        The district-level GADM identifier (e.g. ``"IND.16.13_1"``).

    Returns
    -------
    (geometry, metadata)
        ``geometry`` is a ``shapely.geometry.base.BaseGeometry``
        (``Polygon`` or ``MultiPolygon``). ``metadata`` is a
        :class:`DistrictMetadata` carrying the district and state names
        plus both GID levels.

    Raises
    ------
    DistrictNotFoundError
        If ``gid`` is not present in the ADM_2 layer. The exception
        payload includes a small sample of known GIDs to aid debugging
        without leaking the full 676-district list.
    """
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
