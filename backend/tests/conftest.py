from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from core.config import get_settings
from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort
from infrastructure.db.climate_asset_model import Base

@pytest.fixture(scope="function")
async def in_memory_db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        yield session
        await session.rollback()

    await engine.dispose()


@pytest.fixture
def mock_storage_port():
    mock = MagicMock(spec=StoragePort)
    mock.exists.return_value = False
    return mock


@pytest.fixture
def sample_climate_asset():
    return ClimateAsset(
        id="test-id-123",
        provider="era5",
        variable="temperature",
        year=2024,
        month=6,
        storage_key="era5/temperature/2024/06.nc",
        checksum="abc123xyz",
        file_size=1024,
        status=ClimateAssetStatus.COMPLETED,
        created_at=datetime(2024, 6, 1, 0, 0, 0),
        updated_at=datetime(2024, 6, 1, 0, 0, 0),
    )
