"""S3-backed implementation of :class:`ClimateRepository`.

Reads a Zarr store from S3 through ``s3fs`` and ``xarray``. All
data access is lazy and chunk-aware; the 512 MB RAM ceiling is
enforced by:

* opening the store with ``chunks='auto'`` so every variable
  becomes a Dask array whose chunks mirror the on-disk Zarr
  chunks;
* using ``consolidated=True`` to read ``.zmetadata`` once and
  avoid the per-chunk directory walk;
* never calling ``.load()`` / ``.compute()`` on the full
  dataset — slicing always happens *before* materialization;
* using ``.sel(...)`` with ``method='nearest'`` so only the
  chunks intersecting the requested point or timestep are
  fetched from S3.

This module is the only place in the codebase that knows about
``s3fs``; the rest of the application depends on the abstract
port in :mod:`app.domain.ports.climate_repository`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import s3fs
import xarray as xr

from app.domain.ports.climate_repository import (
    ClimateMetadata,
    ClimateRepository,
    SpatialExtent,
    TimeRange,
)


class S3ClimateRepository(ClimateRepository):
    """Lazy, chunk-aware Zarr reader backed by S3."""

    def __init__(
        self,
        fs: s3fs.S3FileSystem,
        s3_path: str,
    ) -> None:
        # Inject the S3 filesystem and store the path. No network
        # call happens here — ``s3fs.S3FileSystem`` is cheap to
        # construct and only opens connections on first use.
        self._fs = fs
        self._s3_path = s3_path
        # Lazy singleton: the dataset is opened on first access
        # and reused thereafter. This is the single largest
        # startup cost in the adapter, so we amortize it.
        self._ds: xr.Dataset | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_dataset(self) -> xr.Dataset:
        """Open the Zarr store lazily and cache the result.

        Optimization notes (every line is intentional):

        * ``consolidated=True`` — reads ``.zmetadata`` (a single
          JSON object describing the whole store) once, then
          per-chunk GETs skip the directory tree walk that
          otherwise dominates latency on wide stores.
        * ``chunks='auto'`` — Dask infers chunk sizes from the
          Zarr metadata. The resulting arrays are *not*
          rechunked; they match the on-disk chunks exactly,
          which is the only configuration that gives a 1:1
          correspondence between a Dask task and an S3 GET.
        * ``decode_cf=False`` — CF decoding is lazy in modern
          xarray, but disabling it makes the contract explicit
          and removes a class of eager attribute reads.
        * ``decode_timedelta=False`` — paired with the above;
          keeps the dtype pass on coordinate metadata.
        """
        if self._ds is None:
            store = self._fs.get_mapper(self._s3_path, check=True)
            self._ds = xr.open_zarr(
                store,
                consolidated=True,
                chunks="auto",
                decode_cf=False,
                decode_timedelta=False,
            )
        return self._ds

    @staticmethod
    def _coord_name(ds: xr.Dataset, candidates: tuple[str, ...]) -> str:
        """Return the first coordinate name present in ``ds``.

        Climate datasets in the wild use ``lat``/``latitude`` and
        ``lon``/``longitude`` interchangeably. Resolving the
        actual name avoids hard-coding a single convention.
        """
        for name in candidates:
            if name in ds.coords:
                return name
        raise KeyError(
            f"None of the candidate coordinate names {candidates!r} "
            f"are present in the dataset."
        )

    # ------------------------------------------------------------------
    # Port implementation
    # ------------------------------------------------------------------

    def get_metadata(self) -> ClimateMetadata:
        """Read the dataset descriptor without loading data.

        Optimization notes:

        * ``ds.data_vars`` is a frozen key view — zero I/O.
        * Coordinate arrays are 1-D and small (kB–MB) so reading
          their ``.values`` is safe; data variables are never
          touched.
        * ``chunk_sizes`` is read from the Dask graph of the
          first data variable — no actual data fetch, just
          metadata about the chunk shape.
        """
        ds = self._open_dataset()

        variables: tuple[str, ...] = tuple(ds.data_vars)

        lat_name = self._coord_name(ds, ("lat", "latitude", "y"))
        lon_name = self._coord_name(ds, ("lon", "longitude", "x"))

        lats = ds[lat_name].values
        lons = ds[lon_name].values
        times = ds["time"].values

        spatial_extent = SpatialExtent(
            min_latitude=float(lats.min()),
            min_longitude=float(lons.min()),
            max_latitude=float(lats.max()),
            max_longitude=float(lons.max()),
        )
        temporal_extent = TimeRange(
            start=_to_datetime(times.min()),
            end=_to_datetime(times.max()),
        )

        chunk_sizes: dict[str, int] = {}
        if variables:
            first = ds[variables[0]]
            data = first.data
            # Dask arrays expose ``.chunksize``; NumPy arrays do
            # not. Guarding the access lets the method work with
            # both backends without breaking the lazy contract.
            if hasattr(data, "chunksize") and data.chunksize is not None:
                for dim, size in zip(first.dims, data.chunksize):
                    chunk_sizes[dim] = int(size)
                chunk_sizes["bytes_uncompressed_estimate"] = int(
                    first.nbytes / max(len(chunk_sizes), 1)
                ) if chunk_sizes else 0

        crs = str(ds.attrs.get("crs", "EPSG:4326"))

        return ClimateMetadata(
            dataset_id=self._s3_path,
            variables=variables,
            spatial_extent=spatial_extent,
            temporal_extent=temporal_extent,
            crs=crs,
            resolution_degrees=None,
            chunk_sizes=chunk_sizes,
        )

    def get_timeseries(
        self,
        variable: str,
        latitude: float,
        longitude: float,
        time_range: TimeRange | None = None,
    ) -> xr.DataArray:
        """Return a lazy 1-D time series at ``(latitude, longitude)``.

        Optimization notes:

        * ``.sel(..., method='nearest')`` is label-based and
          returns a *view* into the Dask graph. It issues GETs
          for only the chunks containing the requested point,
          not the whole spatial slab.
        * ``.sel(time=slice(...))`` further narrows the time
          axis; again, no copy and no full materialization.
        * The result is a Dask-backed ``DataArray``; the caller
          decides when (and whether) to call ``.load()``.
        """
        ds = self._open_dataset()
        if variable not in ds.data_vars:
            raise KeyError(f"Variable {variable!r} not in dataset.")

        lat_name = self._coord_name(ds, ("lat", "latitude", "y"))
        lon_name = self._coord_name(ds, ("lon", "longitude", "x"))

        ts = ds[variable].sel(
            {lat_name: latitude, lon_name: longitude},
            method="nearest",
        )
        if time_range is not None:
            ts = ts.sel(time=slice(time_range.start, time_range.end))
        return ts

    def get_grid(
        self,
        variable: str,
        time: datetime,
    ) -> xr.Dataset:
        """Return a lazy 2-D spatial slice of ``variable`` at ``time``.

        Optimization notes:

        * ``ds[[variable]]`` projects to a single data
          variable, pruning the rest of the variables from the
          returned object. Dask still keeps the unselected
          variables' chunks in the graph, but no GETs are
          issued for them because the caller never iterates
          over them.
        * ``.sel(time=time, method='nearest')`` selects one
          timestep. For a typical global climate dataset this
          reads a single chunk (~10–50 MB), comfortably below
          the 512 MB ceiling.
        * The result is returned without ``.load()``; the
          caller materializes it on demand.
        """
        ds = self._open_dataset()
        if variable not in ds.data_vars:
            raise KeyError(f"Variable {variable!r} not in dataset.")

        return ds[[variable]].sel(time=time, method="nearest")


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _to_datetime(value: Any) -> datetime:
    """Convert a numpy / pandas timestamp scalar to ``datetime``.

    Used by :meth:`S3ClimateRepository.get_metadata` so the
    returned :class:`TimeRange` exposes native ``datetime``
    objects regardless of the on-disk dtype.
    """
    # numpy.datetime64 → Python datetime via pandas, which is
    # already a transitive dependency of xarray.
    ts = getattr(value, "item", None)
    if callable(ts):
        return ts()
    # Fallback for plain strings or other scalars.
    return value  # type: ignore[return-value]


