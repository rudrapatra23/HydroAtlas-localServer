"""
raster_clip.py
==============
Two-stage district-level raster clipping for HydroAtlas.

Stage 1 – Bounding-box pre-filter
    Compute a padded bounding box around the district polygon and read only
    the raster window that intersects it, avoiding full-raster I/O.

Stage 2 – Exact-polygon mask
    Apply the precise district polygon as a rasterio mask against the bbox
    subset.  Pixels inside the bbox but outside the district are set to
    NoData and excluded from all downstream calculations.

Public API
----------
``DistrictRasterClipper``   – main class; call ``.clip()``
``ClippedRasterResult``     – dataclass carrying the masked array, transform,
                              CRS, nodata value, and spatial metadata.

Helper functions (also importable):
    transform_geometry_to_raster_crs(geom, src_crs, dst_crs)
    get_padded_bbox(geometry, padding)
    bbox_to_raster_window(bbox, raster_transform, raster_width, raster_height)
    read_raster_window(dataset, window, band)
    mask_window_with_exact_geometry(windowed_dataset_or_array,
                                    geometry, window_transform, nodata)

Important correctness invariant
--------------------------------
A bounding box is only a coarse spatial pre-filter.  Statistics must
**never** be computed from raw bbox pixels.  After masking, only pixels
inside the exact district polygon are valid.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Tuple, Union

import numpy as np
import numpy.ma as ma
import rasterio
import rasterio.windows as rio_windows
import rasterio.mask as rio_mask
from rasterio.crs import CRS
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from rasterio.windows import Window
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ClippedRasterResult:
    """
    Carries the output of a two-stage district raster clip.

    Attributes
    ----------
    data : np.ma.MaskedArray
        2-D (or 3-D for multi-band) masked array.  Masked (``True``) entries
        are pixels **outside** the district polygon or original NoData pixels.
        Shape is ``(rows, cols)`` for single-band reads.
    transform : Affine
        Affine geo-transform of *data* (top-left corner of the clipped extent).
    crs : rasterio.crs.CRS
        Coordinate reference system of the raster (unchanged after clipping).
    nodata : float or None
        NoData sentinel value used in the source raster.
    district_id : str
        Identifier of the district that was clipped.
    raster_path : Path
        Source raster file that was read.
    band : int
        Band index (1-based) that was read.
    bbox_used : Tuple[float, float, float, float]
        ``(minx, miny, maxx, maxy)`` of the padded bounding box used for
        the Stage-1 window read (informational only).
    valid_pixel_count : int
        Number of pixels that lie inside the exact district polygon after
        masking (i.e. unmasked pixels).
    """

    data: ma.MaskedArray
    transform: Affine
    crs: CRS
    nodata: Optional[float]
    district_id: str
    raster_path: Path
    band: int
    bbox_used: Tuple[float, float, float, float]
    valid_pixel_count: int = field(init=False)
    # Optional per-cell overlap fractions (float array shape == data.shape)
    overlap_fractions: Optional[np.ndarray] = field(default=None)
    # Optional per-cell intersection geometries (2D object array of Shapely geometries or None)
    cell_geometries: Optional[np.ndarray] = field(default=None)

    def __post_init__(self) -> None:
        self.valid_pixel_count = int(np.sum(~np.asarray(self.data.mask)))

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def valid_data(self) -> np.ndarray:
        """Return a flat 1-D array of all unmasked pixel values."""
        return self.data.compressed()

    def mean(self) -> float:
        """Mean of valid pixels; returns NaN if no valid pixels."""
        vals = self.valid_data
        return float(np.mean(vals)) if len(vals) else float("nan")

    def std(self) -> float:
        """Std-dev of valid pixels; returns NaN if no valid pixels."""
        vals = self.valid_data
        return float(np.std(vals)) if len(vals) else float("nan")

    def min(self) -> float:
        vals = self.valid_data
        return float(np.min(vals)) if len(vals) else float("nan")

    def max(self) -> float:
        vals = self.valid_data
        return float(np.max(vals)) if len(vals) else float("nan")

    def sum(self) -> float:
        vals = self.valid_data
        return float(np.sum(vals)) if len(vals) else float("nan")


# ---------------------------------------------------------------------------
# Step 0 – CRS helpers
# ---------------------------------------------------------------------------

def _parse_crs(crs_input: Union[str, int, CRS]) -> CRS:
    """Normalise a CRS input to a ``rasterio.crs.CRS`` object."""
    if isinstance(crs_input, CRS):
        return crs_input
    if isinstance(crs_input, int):
        return CRS.from_epsg(crs_input)
    return CRS.from_user_input(crs_input)


def _crs_equal(a: CRS, b: CRS) -> bool:
    """Return True if two CRS objects represent the same coordinate system."""
    try:
        return a.to_epsg() == b.to_epsg() and a.to_epsg() is not None
    except Exception:
        return a.to_wkt() == b.to_wkt()


# ---------------------------------------------------------------------------
# Step 1 – Geometry CRS reprojection
# ---------------------------------------------------------------------------

def transform_geometry_to_raster_crs(
    geom: BaseGeometry,
    src_crs: Union[str, int, CRS],
    dst_crs: Union[str, int, CRS],
) -> BaseGeometry:
    """
    Reproject *geom* from *src_crs* to *dst_crs*.

    If the two CRS are equal (or equivalent) the original geometry is
    returned unchanged without any computation.

    Parameters
    ----------
    geom :
        A Shapely geometry (Polygon, MultiPolygon, etc.).
    src_crs :
        CRS of *geom* – EPSG int, WKT string, or ``rasterio.crs.CRS``.
    dst_crs :
        Target CRS (same formats).

    Returns
    -------
    shapely.geometry.BaseGeometry
        Reprojected geometry in *dst_crs*.
    """
    src = _parse_crs(src_crs)
    dst = _parse_crs(dst_crs)

    if _crs_equal(src, dst):
        logger.debug("CRS identical – skipping reprojection.")
        return geom

    logger.debug("Reprojecting geometry from %s to %s", src.to_epsg(), dst.to_epsg())
    geom_dict = mapping(geom)
    reprojected_dict = transform_geom(src, dst, geom_dict)
    return shape(reprojected_dict)


# ---------------------------------------------------------------------------
# Step 2 – Padded bounding box
# ---------------------------------------------------------------------------

def get_padded_bbox(
    geometry: BaseGeometry,
    padding: float = 0.0,
) -> Tuple[float, float, float, float]:
    """
    Return a bounding box ``(minx, miny, maxx, maxy)`` around *geometry*
    with an optional uniform *padding* in the geometry's native units.

    Parameters
    ----------
    geometry :
        A Shapely geometry.
    padding :
        Extra buffer in the geometry's native units (metres for projected
        CRS, degrees for geographic CRS).  Defaults to ``0.0``.

    Returns
    -------
    tuple
        ``(minx, miny, maxx, maxy)``

    Notes
    -----
    The bounding box is a coarse spatial pre-filter only.  It must **not**
    be used to compute final district statistics.
    """
    if geometry.is_empty:
        raise ValueError("Cannot compute bbox of an empty geometry.")

    minx, miny, maxx, maxy = geometry.bounds
    return (
        minx - padding,
        miny - padding,
        maxx + padding,
        maxy + padding,
    )


# ---------------------------------------------------------------------------
# Step 3 – Convert bbox to rasterio Window, clamped to raster extent
# ---------------------------------------------------------------------------

def bbox_to_raster_window(
    bbox: Tuple[float, float, float, float],
    raster_transform: Affine,
    raster_width: int,
    raster_height: int,
) -> Optional[Window]:
    """
    Convert a bounding box in the raster's CRS to a ``rasterio.windows.Window``
    that is clamped to the raster's pixel extent.

    Parameters
    ----------
    bbox :
        ``(minx, miny, maxx, maxy)`` in the raster's CRS units.
    raster_transform :
        Affine transform of the source raster dataset.
    raster_width, raster_height :
        Pixel dimensions of the source raster.

    Returns
    -------
    rasterio.windows.Window or None
        The clamped window, or ``None`` if the bbox does not intersect the
        raster at all.
    """
    minx, miny, maxx, maxy = bbox

    # Compute the Window from geographic bounds
    window = rio_windows.from_bounds(
        left=minx,
        bottom=miny,
        right=maxx,
        top=maxy,
        transform=raster_transform,
    )

    # Intersection with the full raster window to clamp
    full_window = Window(0, 0, raster_width, raster_height)
    try:
        clipped = window.intersection(full_window)
    except rasterio.errors.WindowError:
        # No overlap with the raster extent at all
        logger.warning("Bounding box does not intersect the raster extent.")
        return None

    # Snap to integer pixel offsets (expand outward to avoid sub-pixel gaps)
    col_off = max(0, math.floor(clipped.col_off))
    row_off = max(0, math.floor(clipped.row_off))
    col_end = min(raster_width,  math.ceil(clipped.col_off + clipped.width))
    row_end = min(raster_height, math.ceil(clipped.row_off + clipped.height))

    if col_end <= col_off or row_end <= row_off:
        logger.warning("Clamped raster window is empty (district at raster edge).")
        return None

    return Window(
        col_off=col_off,
        row_off=row_off,
        width=col_end - col_off,
        height=row_end - row_off,
    )


# ---------------------------------------------------------------------------
# Step 4 – Read the raster window
# ---------------------------------------------------------------------------

def read_raster_window(
    dataset: rasterio.DatasetReader,
    window: Window,
    band: int = 1,
) -> Tuple[np.ndarray, Affine]:
    """
    Read a single band from *dataset* within *window* without loading the
    full raster into memory.

    Parameters
    ----------
    dataset :
        An open rasterio dataset (must not be closed).
    window :
        The ``Window`` to read (should already be clamped to dataset bounds).
    band :
        1-based band index.  Defaults to ``1``.

    Returns
    -------
    (array, window_transform)
        ``array`` is a 2-D ``np.ndarray`` of shape ``(rows, cols)``.
        ``window_transform`` is the ``Affine`` transform for the top-left
        pixel of the window.
    """
    if band < 1 or band > dataset.count:
        raise ValueError(
            f"Band {band} is out of range.  Dataset has {dataset.count} band(s)."
        )

    arr = dataset.read(band, window=window)
    window_transform = dataset.window_transform(window)
    logger.debug(
        "Read window shape=%s transform=%s", arr.shape, window_transform
    )
    return arr, window_transform


# ---------------------------------------------------------------------------
# Step 5 – Mask the window with the exact district polygon
# ---------------------------------------------------------------------------

def mask_window_with_exact_geometry(
    window_array: np.ndarray,
    window_transform: Affine,
    geometry: BaseGeometry,
    nodata: Optional[float] = None,
    all_touched: bool = False,
) -> ma.MaskedArray:
    """
    Apply the exact district polygon as a mask over *window_array*.

    Pixels inside the bbox but **outside** *geometry* are masked (set to
    True in the mask) and must not contribute to any statistics.

    Parameters
    ----------
    window_array :
        2-D NumPy array read from the raster window (rows × cols).
    window_transform :
        Affine geo-transform corresponding to *window_array*.
    geometry :
        Shapely geometry representing the exact district boundary.
        May be a ``Polygon`` or ``MultiPolygon``.
    nodata :
        Original NoData sentinel.  Pixels already equal to this value are
        also masked regardless of whether they fall inside the polygon.
    all_touched :
        If True, rasterise using "all touched" rule (pixels touching the
        boundary are included).  Default is centre-point rule.

    Returns
    -------
    numpy.ma.MaskedArray
        2-D masked array.  ``mask=True`` means the pixel is invalid (outside
        district or original NoData).

    Implementation notes
    --------------------
    We avoid creating a temporary in-memory rasterio dataset by using
    ``rasterio.features.geometry_mask`` directly with the window transform.
    """
    from rasterio.features import geometry_mask

    rows, cols = window_array.shape

    # geometry_mask returns True where pixels are OUTSIDE all geometries
    outside_mask = geometry_mask(
        geometries=[mapping(geometry)],
        out_shape=(rows, cols),
        transform=window_transform,
        invert=False,        # True → outside
        all_touched=all_touched,
    )

    # Build the combined mask: outside-polygon OR nodata
    if nodata is not None:
        nodata_mask = (window_array == nodata)
        combined_mask = outside_mask | nodata_mask
    else:
        combined_mask = outside_mask

    masked_arr = ma.array(window_array, mask=combined_mask, fill_value=nodata)
    logger.debug(
        "Masked array: %d valid pixels out of %d total.",
        int(np.sum(~combined_mask)),
        combined_mask.size,
    )
    return masked_arr


def mask_window_with_fractional_geometry(
    window_array: np.ndarray,
    window_transform: Affine,
    geometry: BaseGeometry,
    raster_crs: Union[str, int, CRS] = "EPSG:4326",
    nodata: Optional[float] = None,
    all_touched: bool = False,
) -> tuple[ma.MaskedArray, np.ndarray, np.ndarray]:
    """
    Perform true geometric clipping per raster cell and compute overlap
    fractions and intersection geometries.

    Returns: (masked_array, overlap_fractions, cell_geometries)
      - masked_array : masked array where mask=True for excluded pixels
      - overlap_fractions : float ndarray (rows, cols) in [0,1]
      - cell_geometries : object ndarray (rows, cols) with Shapely geometry
    """
    from shapely.ops import transform as shp_transform
    import pyproj

    rows, cols = window_array.shape

    # Prepare outputs
    overlaps = np.zeros((rows, cols), dtype=float)
    geoms = np.empty((rows, cols), dtype=object)

    # Choose an equal-area projection centred on the district geometry centroid
    centroid = geometry.centroid
    try:
        lon0, lat0 = centroid.x, centroid.y
    except Exception:
        # Fallback centre
        lon0, lat0 = 0.0, 0.0

    # Use Lambert Azimuthal Equal-Area centred on the district
    laea_proj = pyproj.CRS.from_proj4(f"+proj=laea +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m +no_defs")
    src_crs = _parse_crs(raster_crs)
    transformer_to_laea = pyproj.Transformer.from_crs(src_crs, laea_proj, always_xy=True)

    def to_laea(x, y, z=None):
        return transformer_to_laea.transform(x, y)

    # Loop cells and compute intersection
    for r in range(rows):
        for c in range(cols):
            left = window_transform.c + c * window_transform.a
            right = left + window_transform.a
            top = window_transform.f + r * window_transform.e
            bottom = top + window_transform.e

            cell = box(left, bottom, right, top)

            inter = cell.intersection(geometry)
            geoms[r, c] = inter if not inter.is_empty else None

            # Respect nodata: treat nodata cells as excluded
            if nodata is not None and window_array[r, c] == nodata:
                overlaps[r, c] = 0.0
                continue

            if inter.is_empty:
                overlaps[r, c] = 0.0
            else:
                # compute geodesic/projected areas in equal-area CRS
                try:
                    cell_la = shp_transform(to_laea, cell).area
                    inter_la = shp_transform(to_laea, inter).area
                    if cell_la <= 0:
                        overlaps[r, c] = 0.0
                    else:
                        overlaps[r, c] = float(inter_la / cell_la)
                except Exception:
                    overlaps[r, c] = 0.0

    # Build masked array: mask True where overlap == 0 OR nodata
    combined_mask = (overlaps == 0.0)
    if nodata is not None:
        combined_mask = combined_mask | (window_array == nodata)

    masked = ma.array(window_array, mask=combined_mask, fill_value=nodata)

    logger.debug(
        "Fractional mask: %d valid pixels (overlap>0) out of %d total.",
        int(np.sum(overlaps > 0.0)), overlaps.size,
    )
    return masked, overlaps, geoms


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class DistrictRasterClipper:
    """
    Orchestrate the two-stage district-level raster clipping workflow.

    Usage
    -----
    ::

        from hydroatlas.geometry_store import load_districts, get_district_geometry
        from hydroatlas.raster_clip import DistrictRasterClipper

        store = load_districts("districts.geojson")
        clipper = DistrictRasterClipper(
            raster_path="rainfall.tif",
            district_store=store,
            district_crs="EPSG:4326",   # CRS of the geometry store
            padding=0.01,               # ~1 km in degrees
        )
        result = clipper.clip(district_id="KA_05", band=1)

        print(result.mean())            # statistics over the district only
        print(result.valid_pixel_count)

    Parameters
    ----------
    raster_path :
        Path to the source raster file (GeoTIFF or any GDAL-readable format).
    district_store :
        Dict mapping district IDs to GeoJSON features, as returned by
        :func:`~hydroatlas.geometry_store.load_districts`.
    district_crs :
        CRS of the district geometries.  For plain GeoJSON this is
        ``"EPSG:4326"``.
    padding :
        Uniform padding (in the raster's CRS units) added to the district
        bounding box before the Stage-1 window read.  A small value (e.g.
        one or two pixel widths) prevents boundary clipping artefacts.
        Defaults to ``0.0``.
    band :
        Default band (1-based) to read.  Can be overridden per ``.clip()``
        call.
    all_touched :
        Passed to ``mask_window_with_exact_geometry`` – controls boundary
        pixel inclusion rule.
    """

    def __init__(
        self,
        raster_path: Union[str, Path],
        district_store: dict,
        district_crs: Union[str, int, CRS] = "EPSG:4326",
        padding: float = 0.0,
        band: int = 1,
        all_touched: bool = False,
    ) -> None:
        self.raster_path = Path(raster_path)
        if not self.raster_path.exists():
            raise FileNotFoundError(f"Raster not found: {self.raster_path}")

        self.district_store = district_store
        self.district_crs = _parse_crs(district_crs)
        self.padding = padding
        self.default_band = band
        self.all_touched = all_touched

        # Cache raster metadata (opened once) --------------------------------
        with rasterio.open(str(self.raster_path)) as ds:
            self._raster_crs: CRS = ds.crs
            self._raster_transform: Affine = ds.transform
            self._raster_width: int = ds.width
            self._raster_height: int = ds.height
            self._raster_nodata: Optional[float] = ds.nodata
            self._raster_band_count: int = ds.count

        logger.info(
            "DistrictRasterClipper initialised: raster=%s  CRS=%s  size=%dx%d  bands=%d",
            self.raster_path.name,
            self._raster_crs.to_string(),
            self._raster_width,
            self._raster_height,
            self._raster_band_count,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def clip(
        self,
        district_id: str,
        band: Optional[int] = None,
        padding: Optional[float] = None,
    ) -> ClippedRasterResult:
        """
        Execute the two-stage clip for *district_id*.

        Parameters
        ----------
        district_id :
            Unique identifier of the district to clip.
        band :
            1-based band index.  Overrides the instance-level default.
        padding :
            Bounding-box padding in the raster's CRS units.  Overrides the
            instance-level default.

        Returns
        -------
        ClippedRasterResult
            Contains the masked array, transform, CRS, metadata, and
            convenience statistics methods.

        Raises
        ------
        KeyError
            If *district_id* is not in the district store.
        ValueError
            If the raster window is empty (district entirely outside raster).
        """
        band = band if band is not None else self.default_band
        padding = padding if padding is not None else self.padding

        # ---- Step A: Get the district geometry ---------------------------
        from hydroatlas.geometry_store import get_district_geometry
        raw_geom = get_district_geometry(self.district_store, district_id)

        # ---- Step B: Reproject to raster CRS if needed -------------------
        geom_in_raster_crs = transform_geometry_to_raster_crs(
            raw_geom,
            src_crs=self.district_crs,
            dst_crs=self._raster_crs,
        )

        # ---- Step C: Padded bounding box (Stage 1 pre-filter) ------------
        bbox = get_padded_bbox(geom_in_raster_crs, padding=padding)
        logger.info(
            "[%s] Padded bbox (raster CRS): minx=%.6f miny=%.6f maxx=%.6f maxy=%.6f",
            district_id, *bbox,
        )

        # ---- Step D: Convert bbox to pixel Window, clamped ---------------
        window = bbox_to_raster_window(
            bbox=bbox,
            raster_transform=self._raster_transform,
            raster_width=self._raster_width,
            raster_height=self._raster_height,
        )
        if window is None:
            raise ValueError(
                f"District '{district_id}' does not intersect the raster extent.  "
                "No pixels available for clipping."
            )

        # ---- Step E: Read only the window from disk ----------------------
        with rasterio.open(str(self.raster_path)) as ds:
            window_array, window_transform = read_raster_window(ds, window, band)

        # ---- Step F: Exact-polygon mask (Stage 2) -------------------------
        masked = mask_window_with_exact_geometry(
            window_array=window_array,
            window_transform=window_transform,
            geometry=geom_in_raster_crs,
            nodata=self._raster_nodata,
            all_touched=self.all_touched,
        )

        logger.info(
            "[%s] Clip complete – %d valid pixels (band %d).",
            district_id, int(np.sum(~np.asarray(masked.mask))), band,
        )

        return ClippedRasterResult(
            data=masked,
            transform=window_transform,
            crs=self._raster_crs,
            nodata=self._raster_nodata,
            district_id=district_id,
            raster_path=self.raster_path,
            band=band,
            bbox_used=bbox,
        )

    # ------------------------------------------------------------------
    # Multi-variable convenience wrapper
    # ------------------------------------------------------------------

    def clip_multiple_rasters(
        self,
        district_id: str,
        raster_paths: Sequence[Union[str, Path]],
        band: int = 1,
        padding: Optional[float] = None,
    ) -> dict[str, ClippedRasterResult]:
        """
        Clip the same district from multiple raster files (e.g. rainfall,
        soil moisture, temperature) using the same geometry and bbox.

        Returns
        -------
        dict
            ``{str(raster_path): ClippedRasterResult}``
        """
        results: dict[str, ClippedRasterResult] = {}
        for rp in raster_paths:
            rp = Path(rp)
            # Temporarily swap the raster path and re-read its metadata
            original_path = self.raster_path
            original_meta = (
                self._raster_crs,
                self._raster_transform,
                self._raster_width,
                self._raster_height,
                self._raster_nodata,
                self._raster_band_count,
            )
            try:
                self.raster_path = rp
                with rasterio.open(str(rp)) as ds:
                    self._raster_crs = ds.crs
                    self._raster_transform = ds.transform
                    self._raster_width = ds.width
                    self._raster_height = ds.height
                    self._raster_nodata = ds.nodata
                    self._raster_band_count = ds.count
                results[str(rp)] = self.clip(district_id, band=band, padding=padding)
            finally:
                self.raster_path = original_path
                (
                    self._raster_crs,
                    self._raster_transform,
                    self._raster_width,
                    self._raster_height,
                    self._raster_nodata,
                    self._raster_band_count,
                ) = original_meta

        return results
