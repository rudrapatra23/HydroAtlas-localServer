"""
netcdf_reader.py
================
Read and spatially subset ERA5-style NetCDF files (variable × time_slice)
and convert a bbox/lat-lon window into a rasterio-compatible 2D array +
Affine transform for use with the existing two-stage district clipping pipeline.

Key design notes
----------------
* The ERA5 file uses:
  - CRS:       EPSG:4326 (geographic, degrees)
  - Longitude: 0–360 convention (0.0 to 359.9)
  - Latitude:  *decreasing* (90.0 → -90.0), step = -0.1°
* The raster_clip pipeline expects an Affine transform with a negative y-step
  (top-left origin, row 0 = maximum latitude) — ERA5 already satisfies this.
* Longitude wrapping: for India (68–97°E), all longitudes are < 360 and
  require no wrapping; the same 0–360 values are used directly.

Public API
----------
inspect_netcdf(path)
    Print/return a metadata summary dict.

read_netcdf_as_array(path, variable, time_index, bbox)
    Return (array_2d, affine_transform, crs, fill_value, metadata_dict).
    ``bbox`` must be (minx, miny, maxx, maxy) in EPSG:4326 degrees,
    using the 0–360 longitude convention when applicable.

bbox_to_lonlat_convention(minx, miny, maxx, maxy, lon_array)
    Convert a standard -180/+180 bbox to the file's longitude convention.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import netCDF4 as nc4
import numpy as np
from rasterio.crs import CRS
from rasterio.transform import from_bounds as affine_from_bounds

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def inspect_netcdf(path: Union[str, Path]) -> Dict[str, Any]:
    """
    Open *path* and return a structured metadata summary.

    Returns
    -------
    dict with keys:
        ``dimensions``, ``variables``, ``global_attrs``,
        ``lat_range``, ``lat_direction``, ``lat_step``,
        ``lon_range``, ``lon_step``, ``lon_convention``,
        ``time_values``, ``time_decoded``
    """
    path = Path(path)
    meta: Dict[str, Any] = {}

    with nc4.Dataset(str(path)) as ds:
        meta["dimensions"] = {k: len(v) for k, v in ds.dimensions.items()}
        meta["global_attrs"] = {k: str(ds.getncattr(k)) for k in ds.ncattrs()}

        vars_meta: Dict[str, Any] = {}
        for name, var in ds.variables.items():
            attrs = {k: var.getncattr(k) for k in var.ncattrs()}
            vars_meta[name] = {
                "shape": var.shape,
                "dtype": str(var.dtype),
                "dims": var.dimensions,
                "attrs": attrs,
            }
        meta["variables"] = vars_meta

        lat = np.array(ds.variables["latitude"][:])
        lon = np.array(ds.variables["longitude"][:])

        meta["lat_range"] = (float(lat.min()), float(lat.max()))
        meta["lat_direction"] = "decreasing" if lat[0] > lat[-1] else "increasing"
        meta["lat_step"] = float(np.diff(lat[:5]).mean())

        meta["lon_range"] = (float(lon.min()), float(lon.max()))
        meta["lon_step"] = float(np.diff(lon[:5]).mean())
        meta["lon_convention"] = "0-360" if lon.max() > 180.0 else "-180-180"

        if "valid_time" in ds.variables:
            t_arr = np.array(ds.variables["valid_time"][:])
            meta["time_values"] = t_arr.tolist()
            import datetime
            meta["time_decoded"] = [
                str(datetime.datetime.fromtimestamp(int(t), tz=datetime.timezone.utc))
                for t in t_arr
            ]
        else:
            meta["time_values"] = []
            meta["time_decoded"] = []

    return meta


def bbox_to_lonlat_convention(
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    lon_array: np.ndarray,
) -> Tuple[float, float, float, float]:
    """
    Convert a bbox in standard -180/+180 longitude to the file's convention.

    If the file uses 0–360 and the bbox longitudes are already in 0–360
    (all positive and ≤ 360), they are returned unchanged.
    If the bbox uses -180/+180 and the file uses 0–360, negative longitudes
    are shifted by +360.

    Parameters
    ----------
    minx, miny, maxx, maxy :
        Input bounding box (degrees).
    lon_array :
        The file's longitude coordinate array.

    Returns
    -------
    (minx, miny, maxx, maxy) in the file's convention.
    """
    file_uses_360 = lon_array.max() > 180.0
    if file_uses_360:
        if minx < 0:
            minx += 360.0
        if maxx < 0:
            maxx += 360.0
    return minx, miny, maxx, maxy


def read_netcdf_as_array(
    path: Union[str, Path],
    variable: str,
    time_index: int,
    bbox: Tuple[float, float, float, float],
) -> Tuple[np.ndarray, Any, CRS, Optional[float], Dict[str, Any]]:
    """
    Read a single variable / time-slice from an ERA5-style NetCDF,
    spatially subsetted to *bbox*, and return it in a rasterio-compatible
    form (2-D array with top-left Affine transform).

    Parameters
    ----------
    path :
        Path to the NetCDF file.
    variable :
        Variable name (e.g. ``"tp"``, ``"swvl1"``, ``"sro"``).
    time_index :
        0-based index into the ``valid_time`` dimension.
    bbox :
        ``(minx, miny, maxx, maxy)`` in EPSG:4326 degrees, using the
        *file's* longitude convention (0–360 for ERA5).

    Returns
    -------
    array :
        2-D ``np.ndarray`` of shape ``(n_lat, n_lon)`` with the top row
        corresponding to the northernmost latitude in the bbox.
    affine_transform :
        ``rasterio.transform.Affine`` aligned to *array*.
    crs :
        ``rasterio.crs.CRS`` — always ``EPSG:4326`` for ERA5.
    fill_value :
        The variable's ``_FillValue`` (or NaN if absent).
    metadata :
        Dict with ``variable``, ``time_index``, ``time_decoded``,
        ``bbox_used``, ``n_lat``, ``n_lon``, ``lat_step``, ``lon_step``,
        ``units``, ``long_name``.

    Notes
    -----
    Latitude direction
        ERA5 latitudes are stored *decreasing* (90 → -90, step −0.1°).
        After index selection the array rows are already north-first.
        The resulting Affine transform has a negative y-pixel-size, which
        is the standard rasterio convention.

    Longitude convention
        ERA5 uses 0–360.  India's longitudes (68–97°E) fall within this
        range and do not require wrapping.
    """
    path = Path(path)
    minx, miny, maxx, maxy = bbox

    with nc4.Dataset(str(path)) as ds:
        if variable not in ds.variables:
            available = [k for k in ds.variables if k not in ds.dimensions]
            raise KeyError(
                f"Variable '{variable}' not found.  Available: {available}"
            )

        lat_arr = np.array(ds.variables["latitude"][:])
        lon_arr = np.array(ds.variables["longitude"][:])

        # Convert bbox to file's lon convention if needed
        minx, miny, maxx, maxy = bbox_to_lonlat_convention(
            minx, miny, maxx, maxy, lon_arr
        )

        # --- Latitude indexing -------------------------------------------
        # lat_arr is decreasing (90 → -90); miny≤lat≤maxy
        lat_mask = (lat_arr >= miny) & (lat_arr <= maxy)
        lat_idx = np.where(lat_mask)[0]
        if len(lat_idx) == 0:
            raise ValueError(
                f"No latitude values in [{miny}, {maxy}].  "
                f"File lat range: [{lat_arr.min():.2f}, {lat_arr.max():.2f}]"
            )
        lat_start = int(lat_idx.min())
        lat_end   = int(lat_idx.max()) + 1  # exclusive

        # --- Longitude indexing ------------------------------------------
        lon_mask = (lon_arr >= minx) & (lon_arr <= maxx)
        lon_idx = np.where(lon_mask)[0]
        if len(lon_idx) == 0:
            raise ValueError(
                f"No longitude values in [{minx}, {maxx}].  "
                f"File lon range: [{lon_arr.min():.2f}, {lon_arr.max():.2f}]"
            )
        lon_start = int(lon_idx.min())
        lon_end   = int(lon_idx.max()) + 1  # exclusive

        # --- Read the variable slice -------------------------------------
        var_obj = ds.variables[variable]
        dims = var_obj.dimensions
        n_dims = len(dims)

        if n_dims == 3:
            # (time, latitude, longitude)
            data_slice = var_obj[time_index, lat_start:lat_end, lon_start:lon_end]
        elif n_dims == 2:
            # (latitude, longitude)
            data_slice = var_obj[lat_start:lat_end, lon_start:lon_end]
        else:
            raise ValueError(
                f"Variable '{variable}' has {n_dims} dims {dims}; "
                "expected 2 or 3."
            )

        arr_2d = np.array(data_slice, dtype=np.float64)

        # Retrieve fill value
        try:
            fill_value = float(var_obj.getncattr("_FillValue"))
        except AttributeError:
            fill_value = None

        # Replace fill values with NaN
        if fill_value is not None:
            arr_2d[arr_2d == fill_value] = np.nan

        # Scale / offset (ERA5 cfgrib output is already physical — no scaling)
        try:
            scale = float(var_obj.getncattr("scale_factor"))
            arr_2d = arr_2d * scale
        except AttributeError:
            pass
        try:
            offset = float(var_obj.getncattr("add_offset"))
            arr_2d = arr_2d + offset
        except AttributeError:
            pass

        # --- Sub-selected lat/lon arrays --------------------------------
        sub_lat = lat_arr[lat_start:lat_end]  # decreasing
        sub_lon = lon_arr[lon_start:lon_end]  # increasing

        n_lat, n_lon = arr_2d.shape

        # lat_arr is decreasing → sub_lat[0] is northernmost (top row)
        # Build Affine: top-left pixel center is (sub_lon[0], sub_lat[0])
        # Resolution: lon_step positive, lat_step negative
        lon_step = float(np.diff(sub_lon[:2]).mean()) if n_lon > 1 else (lon_arr[1] - lon_arr[0])
        lat_step = float(np.diff(sub_lat[:2]).mean()) if n_lat > 1 else (lat_arr[1] - lat_arr[0])
        # lat_step is negative (decreasing)

        # Affine top-left corner = center of top-left pixel
        # rasterio convention: transform maps pixel top-left corner
        # So shift by half-pixel
        west  = float(sub_lon[0])  - lon_step * 0.5
        north = float(sub_lat[0])  - lat_step * 0.5  # sub lat_step < 0, so this goes further north

        from rasterio.transform import from_origin
        affine_transform = from_origin(
            west=west,
            north=north,
            xsize=lon_step,
            ysize=-lat_step,  # from_origin takes positive ysize
        )

        # Build metadata
        import datetime
        time_decoded = None
        if "valid_time" in ds.variables:
            t_val = int(np.array(ds.variables["valid_time"][:])[time_index])
            time_decoded = str(datetime.datetime.fromtimestamp(t_val, tz=datetime.timezone.utc))

        try:
            units = var_obj.getncattr("units")
        except AttributeError:
            units = "unknown"
        try:
            long_name = var_obj.getncattr("long_name")
        except AttributeError:
            long_name = variable

        metadata = {
            "variable": variable,
            "long_name": long_name,
            "units": units,
            "time_index": time_index,
            "time_decoded": time_decoded,
            "bbox_used": (minx, miny, maxx, maxy),
            "n_lat": n_lat,
            "n_lon": n_lon,
            "lat_step": lat_step,
            "lon_step": lon_step,
            "lat_range_used": (float(sub_lat.min()), float(sub_lat.max())),
            "lon_range_used": (float(sub_lon.min()), float(sub_lon.max())),
        }

        crs = CRS.from_epsg(4326)
        logger.info(
            "Read NetCDF: var=%s  time=%s  shape=%s  lat=[%.3f,%.3f]  lon=[%.3f,%.3f]",
            variable, time_decoded, arr_2d.shape,
            float(sub_lat.min()), float(sub_lat.max()),
            float(sub_lon.min()), float(sub_lon.max()),
        )

        return arr_2d, affine_transform, crs, float("nan"), metadata
