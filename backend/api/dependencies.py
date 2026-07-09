from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from application.dataset_service import DatasetService
from core.config import Settings, get_settings
from district_clip import Era5DistrictClipper
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort
from infrastructure.db.session import get_session
from infrastructure.repositories.postgres_dataset_repository import (
    PostgresDatasetRepository,
)
from infrastructure.storage.s3_storage_adapter import S3StorageAdapter


async def get_repository(
    session: Annotated[AsyncSession, Depends(get_session)]
) -> DatasetRepository:
    return PostgresDatasetRepository(session)


async def get_storage() -> StoragePort:
    return S3StorageAdapter()


async def get_dataset_service(
    repository: Annotated[DatasetRepository, Depends(get_repository)],
) -> DatasetService:
    return DatasetService(repository)


async def get_district_clipper(
    repository: Annotated[DatasetRepository, Depends(get_repository)],
    storage: Annotated[StoragePort, Depends(get_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Era5DistrictClipper:
    """Build a per-request ``Era5DistrictClipper`` for the raster-clip endpoint.

    The clipper is stateless, so constructing it per request is cheap and
    keeps the dependency-injection graph simple.  The underlying
    :class:`RasterCache` is a module-level singleton shared with the
    existing ``/districts/{id}/statistics`` and ``/time-series`` paths,
    so cache hits for the same NetCDF asset are reused automatically.
    """
    return Era5DistrictClipper(
        repository=repository,
        storage=storage,
        raster_cache=None,  # default singleton RasterCache
        max_features=settings.era5_district_clip_max_features,
    )
