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
    SqlAlchemyDatasetRepository,
)
from infrastructure.storage.local_storage_adapter import LocalStorageAdapter


async def get_repository(
    session: Annotated[AsyncSession, Depends(get_session)]
) -> DatasetRepository:
    return SqlAlchemyDatasetRepository(session)


async def get_storage() -> StoragePort:
    return LocalStorageAdapter()


async def get_dataset_service(
    repository: Annotated[DatasetRepository, Depends(get_repository)],
) -> DatasetService:
    return DatasetService(repository)


async def get_district_clipper(
    repository: Annotated[DatasetRepository, Depends(get_repository)],
    storage: Annotated[StoragePort, Depends(get_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Era5DistrictClipper:
    """Creates a new `Era5DistrictClipper` for each request to handle raster clipping.

    The clipper itself doesn't store any state, so making one for every request 
    is light on resources and keeps our dependencies clean. We share a single 
    `RasterCache` across the whole app, which means if different parts of the 
    system need the same data, they can just grab it from the cache instead 
    of downloading it again.
    """
    return Era5DistrictClipper(
        repository=repository,
        storage=storage,
        raster_cache=None,  # default singleton RasterCache
        max_features=settings.era5_district_clip_max_features,
    )
