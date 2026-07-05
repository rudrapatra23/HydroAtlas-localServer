from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Iterable

import xarray as xr

from ingestion.era5.checksums import sha256_file


_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Era5Variable:
    """An ERA5 variable plus the short names used inside CDS NetCDF bundles.

    ``aliases`` enumerates every accepted short name so the splitter can
    recognise a variable regardless of which CDS short name a downloaded
    bundle happens to use. Historically surface runoff was emitted as
    ``ro``; the modern CDS API emits ``sro``. Both must map to the same
    logical variable. ``alias`` returns the canonical (first) alias and
    is preserved for callers that read the singular attribute.
    """
    name: str
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Accept ``str`` or any ``Iterable[str]``; normalise to a
        # deduplicated tuple while preserving declaration order.
        if isinstance(self.aliases, str):
            raw: tuple[str, ...] = (self.aliases,)
        elif self.aliases is None:
            raw = ()
        else:
            raw = tuple(self.aliases)
        seen: list[str] = []
        for a in raw:
            if not isinstance(a, str):
                raise TypeError(
                    f"Era5Variable({self.name!r}) aliases must be strings, "
                    f"got {type(a).__name__}"
                )
            if a and a not in seen:
                seen.append(a)
        if not seen:
            raise ValueError(
                f"Era5Variable({self.name!r}) must declare at least one alias"
            )
        object.__setattr__(self, "aliases", tuple(seen))

    @property
    def alias(self) -> str:
        """Canonical short name (first alias). Preserved for back-compat."""
        return self.aliases[0]


DEFAULT_ERA5_VARIABLES: tuple[Era5Variable, ...] = (
    Era5Variable(name="total_precipitation", aliases=("tp",)),
    Era5Variable(name="volumetric_soil_water_layer_1", aliases=("swvl1",)),
    # ``ro`` is the legacy CDS short name; ``sro`` is what the modern CDS
    # API emits for ``surface_runoff``. Both must map to the same variable.
    Era5Variable(name="surface_runoff", aliases=("ro", "sro")),
)


VARIABLE_CATEGORY: dict[str, str] = {
    "total_precipitation": "precipitation",
    "volumetric_soil_water_layer_1": "soil_moisture",
    "surface_runoff": "surface_runoff",
}


@dataclass(frozen=True)
class SplitFile:
    variable: str
    category: str
    path: Path
    file_size: int
    checksum: str


class DatasetSplitter:
    """Splits a multi-variable ERA5 NetCDF bundle into per-variable files."""

    def __init__(self, era5_variables: tuple[Era5Variable, ...] = DEFAULT_ERA5_VARIABLES) -> None:
        self._era5_variables = era5_variables
        self._logger = logging.getLogger(__name__)

    def split(
        self,
        source: Path,
        year: int,
        month: int,
        temp_dir: Path,
    ) -> list[SplitFile]:
        ds = xr.open_dataset(source, engine="netcdf4")

        variable_encodings: dict[str, dict[str, object]] = {}
        for var_name in ds.data_vars:
            enc = dict(ds[var_name].encoding)
            enc.pop("source", None)
            enc.pop("unlimited_dims", None)
            variable_encodings[var_name] = enc

        global_attrs = dict(ds.attrs)

        # Build a lookup from every accepted short name to its logical
        # variable. A single Era5Variable may declare multiple aliases
        # (e.g. surface_runoff accepts both ``ro`` and ``sro``); all of
        # them must resolve to the same variable.
        var_alias_map: dict[str, Era5Variable] = {
            alias: var for var in self._era5_variables for alias in var.aliases
        }

        split_files: list[SplitFile] = []
        for var_name in ds.data_vars:
            era5_var = var_alias_map.get(var_name)
            if era5_var is None:
                self._logger.warning(
                    "Skipping unknown NetCDF data_var %r in %s; "
                    "expected one of %s",
                    var_name,
                    source.name,
                    sorted(var_alias_map.keys()),
                )
                continue

            category = VARIABLE_CATEGORY.get(era5_var.name, era5_var.name)

            single = ds[[var_name]].copy(deep=False)
            single.attrs = dict(global_attrs)

            if var_name in variable_encodings:
                for k, v in variable_encodings[var_name].items():
                    single[var_name].encoding[k] = v

            filename = f"{category}_{year:04d}_{month:02d}.nc"
            temp_path = temp_dir / filename

            single.to_netcdf(
                str(temp_path),
                engine="netcdf4",
                unlimited_dims=None,
            )

            file_size = temp_path.stat().st_size
            checksum = sha256_file(temp_path)

            split_files.append(
                SplitFile(
                    variable=var_name,
                    category=category,
                    path=temp_path,
                    file_size=file_size,
                    checksum=checksum,
                )
            )

        ds.close()
        return split_files
