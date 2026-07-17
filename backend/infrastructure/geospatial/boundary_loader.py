
from __future__ import annotations

from pathlib import Path
from typing import Optional

import geopandas as gpd

_GADM_PATH = Path(__file__).parent.parent.parent / "data" / "boundaries" / "gadm41_IND.gpkg"

# Cached GeoDataFrames
_ADM1_GDF: Optional[gpd.GeoDataFrame] = None
_ADM2_GDF: Optional[gpd.GeoDataFrame] = None


def get_adm1() -> gpd.GeoDataFrame:
    """Get gadm adm_1 (states/uts) layer, cached on first load."""
    global _ADM1_GDF
    if _ADM1_GDF is None:
        _ADM1_GDF = gpd.read_file(_GADM_PATH, layer="ADM_ADM_1")
    return _ADM1_GDF


def get_adm2() -> gpd.GeoDataFrame:
    """Get gadm adm_2 (districts) layer, cached on first load."""
    global _ADM2_GDF
    if _ADM2_GDF is None:
        _ADM2_GDF = gpd.read_file(_GADM_PATH, layer="ADM_ADM_2")
    return _ADM2_GDF

