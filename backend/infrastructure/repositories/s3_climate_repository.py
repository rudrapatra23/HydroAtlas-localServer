from __future__ import annotations

from datetime import datetime
from typing import Any

import s3fs
import xarray as xr

from domain.ports.climate_repository import (
    ClimateMetadata,
    ClimateRepository,
    SpatialExtent,
    TimeRange,
)


class S3ClimateRepository(ClimateRepository):
    def __init__(
        self,
        fs: s3fs.S3FileSystem,
        s3_path: str,
    ) -> None:
        self._fs = fs
        self._s3_path = s3_path
        self._ds: xr.Dataset | None = None

    def _open_dataset(self) -> xr.Dataset:
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
        for name in candidates:
            if name in ds.coords:
                return name
        raise KeyError(
            f"None of the candidate coordinate names {candidates!r} "
            f"are present in the dataset."
        )

    def get_metadata(self) -> ClimateMetadata:
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
        ds = self._open_dataset()
        if variable not in ds.data_vars:
            raise KeyError(f"Variable {variable!r} not in dataset.")

        return ds[[variable]].sel(time=time, method="nearest")


def _to_datetime(value: Any) -> datetime:
    ts = getattr(value, "item", None)
    if callable(ts):
        return ts()
    return value  # type: ignore[return-value]


