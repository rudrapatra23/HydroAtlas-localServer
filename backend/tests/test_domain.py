import pytest
from datetime import datetime
from dataclasses import FrozenInstanceError

from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus


def test_climate_asset_period_property(sample_climate_asset):
    assert sample_climate_asset.period == "2024-06"


def test_climate_asset_filename_property(sample_climate_asset):
    assert sample_climate_asset.filename == "era5_temperature_2024_06.nc"


def test_climate_asset_frozen_dataclass(sample_climate_asset):
    with pytest.raises(FrozenInstanceError):
        sample_climate_asset.year = 2025
