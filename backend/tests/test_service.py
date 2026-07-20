import pytest

from application.dataset_service import DatasetService
from infrastructure.repositories.postgres_dataset_repository import PostgresDatasetRepository


@pytest.mark.asyncio
async def test_service_get_asset_not_found(in_memory_db_session):
    repo = PostgresDatasetRepository(in_memory_db_session)
    service = DatasetService(repo)

    result = await service.get_asset("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_service_list_assets_empty(in_memory_db_session):
    repo = PostgresDatasetRepository(in_memory_db_session)
    service = DatasetService(repo)

    assets = await service.list_assets()
    assert assets == []
