import pytest
from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from infrastructure.repositories.postgres_dataset_repository import PostgresDatasetRepository


@pytest.mark.asyncio
async def test_repository_save_and_get(in_memory_db_session, sample_climate_asset):
    repo = PostgresDatasetRepository(in_memory_db_session)
    saved = await repo.save(sample_climate_asset)
    assert saved.id is not None

    retrieved = await repo.get_by_id(saved.id)
    assert retrieved is not None
    assert retrieved.provider == sample_climate_asset.provider
    assert retrieved.variable == sample_climate_asset.variable


@pytest.mark.asyncio
async def test_repository_list(in_memory_db_session, sample_climate_asset):
    repo = PostgresDatasetRepository(in_memory_db_session)
    await repo.save(sample_climate_asset)
    assets = await repo.list()
    assert len(assets) == 1


@pytest.mark.asyncio
async def test_repository_exists(in_memory_db_session, sample_climate_asset):
    repo = PostgresDatasetRepository(in_memory_db_session)
    await repo.save(sample_climate_asset)
    exists = await repo.exists("era5", "temperature", 2024, 6)
    assert exists is True
    not_exists = await repo.exists("era5", "precipitation", 2024, 6)
    assert not_exists is False


@pytest.mark.asyncio
async def test_repository_delete(in_memory_db_session, sample_climate_asset):
    repo = PostgresDatasetRepository(in_memory_db_session)
    saved = await repo.save(sample_climate_asset)
    await repo.delete(saved.id)
    retrieved = await repo.get_by_id(saved.id)
    assert retrieved is None
