"""
HydroAtlas — district-level ERA5 raster clipping.

This package is the namespaced home of the validated two-stage (bbox ->
exact-polygon) clipping pipeline that was originally developed in the
``raster-dist`` standalone prototype. It is integrated into the
HydroAtlas backend without rewriting the validated algorithm:

  * ``netcdf_reader`` — bbox-subsetted NetCDF read with ERA5 lon/scale/
    offset/fill handling.
  * ``raster_clip``   — ``mask_window_with_fractional_geometry`` and the
    ``ClippedRasterResult`` container.
  * ``stats``         — overlap-weighted statistics.
  * ``gadm_lookup``   — thin shim over the existing
    ``infrastructure.geospatial.boundary_loader.get_adm2``.
  * ``era5_district_clipper`` — orchestrator that wires the above to the
    existing ``StoragePort`` + ``RasterCache`` + ``Repository``.

Public API
----------
``Era5DistrictClipper``
    Main entry point; call ``.clip()``.
``DistrictClipResult``
    Dataclass carrying the GeoJSON FeatureCollection, summary stats,
    diagnostics and the underlying asset provenance.
"""

from .era5_district_clipper import Era5DistrictClipper, DistrictClipResult
from .gadm_lookup import lookup_district, DistrictNotFoundError

__all__ = [
    "Era5DistrictClipper",
    "DistrictClipResult",
    "lookup_district",
    "DistrictNotFoundError",
]
