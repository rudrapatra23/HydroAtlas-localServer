"""ERA5-Land ingestion module.

Internal ingestion package for downloading ERA5-Land monthly hydrology bundles
from the Copernicus Climate Data Store, splitting them into per-variable
NetCDF files, uploading them to S3, and registering them in HydroAtlas's
existing ``climate_assets`` PostgreSQL table.

Reuses HydroAtlas's configuration (``core.config.get_settings``), S3 adapter
(``infrastructure.storage.S3StorageAdapter``), and database repository
(``infrastructure.repositories.PostgresDatasetRepository``) directly —
no thin wrapper classes.
"""

from ingestion.era5.downloader import CdsClient, DatasetHandle, Downloader

__all__ = ["CdsClient", "DatasetHandle", "Downloader"]
