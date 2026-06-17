"""Abstract port for climate data access.

This module defines the dependency-inversion boundary between the
domain layer and the infrastructure layer. The application/domain
code depends only on :class:`ClimateRepository`; concrete adapters
(S3, GCS, in-memory mocks, etc.) live in the infrastructure layer
and conform to this contract.

The interface is intentionally library-agnostic at runtime:
``xarray`` types are referenced only under ``TYPE_CHECKING`` so
importing this module does not pull a heavy data stack into the
domain layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Imported solely for the type checker; the port itself does
    # not require xarray at runtime. This is what keeps the
    # domain layer free of infrastructure concerns.
    import xarray as xr


# ---------------------------------------------------------------------------
# Domain value objects
#
# Frozen dataclasses with ``slots=True`` are pure data carriers.
# They have no behavior (no business logic) and exist only to
# describe the contract surface of the port.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimeRange:
    """Inclusive temporal window."""

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class SpatialExtent:
    """Geographic bounding box in degrees (WGS84 by convention)."""

    min_latitude: float
    min_longitude: float
    max_latitude: float
    max_longitude: float


@dataclass(frozen=True, slots=True)
class ClimateMetadata:
    """Static descriptor of a climate dataset.

    Implementations must compute this without loading data
    variables — only coordinates and Zarr group metadata are
    permitted as inputs.
    """

    dataset_id: str
    variables: tuple[str, ...]
    spatial_extent: SpatialExtent
    temporal_extent: TimeRange
    crs: str
    resolution_degrees: float | None
    chunk_sizes: dict[str, int]


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


class ClimateRepository(ABC):
    """Abstract gateway to a chunked, gridded climate dataset.

    **Memory contract.** Every method must be lazy. No
    implementation may call ``.load()`` / ``.compute()`` on the
    full dataset. The hard ceiling of 512 MB RAM is enforced by
    returning Dask-backed ``xarray`` objects and letting the
    caller decide when (and how much) to materialize.
    """

    @abstractmethod
    def get_metadata(self) -> ClimateMetadata:
        """Return the static descriptor of the dataset.

        Must read only coordinate arrays, Zarr ``.zattrs``, and
        the variable key list. Touching data variables is a
        contract violation.
        """

    @abstractmethod
    def get_timeseries(
        self,
        variable: str,
        latitude: float,
        longitude: float,
        time_range: TimeRange | None = None,
    ) -> "xr.DataArray":
        """Return the 1-D time series of ``variable`` at a point.

        Args:
            variable: Name of a data variable present in the
                dataset.
            latitude: Latitude in degrees.
            longitude: Longitude in degrees.
            time_range: Optional inclusive window; ``None`` means
                the full temporal axis.

        Returns:
            A lazy ``xarray.DataArray`` indexed by time. The
            returned object must be Dask-backed so the caller
            controls materialization.
        """

    @abstractmethod
    def get_grid(
        self,
        variable: str,
        time: datetime,
    ) -> "xr.Dataset":
        """Return the 2-D spatial slice of ``variable`` at ``time``.

        Args:
            variable: Name of a data variable present in the
                dataset.
            time: Timestamp of the slice (``method='nearest'`` is
                acceptable when the timestamp is not on-axis).

        Returns:
            A lazy ``xarray.Dataset`` containing exactly one
            data variable and the spatial coordinates. Must be
            Dask-backed.
        """
```

