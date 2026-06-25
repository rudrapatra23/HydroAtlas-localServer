from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from application.dataset_service import DatasetService
from application.providers.era5_provider import ERA5Provider, Provider
from core.config import Settings, get_settings
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


async def get_provider() -> Provider:
    return ERA5Provider()


async def get_dataset_service(
    repository: Annotated[DatasetRepository, Depends(get_repository)],
    storage: Annotated[StoragePort, Depends(get_storage)],
    provider: Annotated[Provider, Depends(get_provider)],
) -> DatasetService:
    return DatasetService(repository, storage, provider)
