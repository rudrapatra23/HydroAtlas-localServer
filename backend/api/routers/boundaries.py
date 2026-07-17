
from __future__ import annotations

import json
from typing import Sequence

from fastapi import APIRouter, HTTPException

from infrastructure.geospatial.boundary_loader import get_adm1, get_adm2

router = APIRouter(prefix="/boundaries", tags=["boundaries"])


@router.get("/states")
def list_states() -> Sequence[dict]:
    """Get a list of all states and union territories in India."""
    gdf = get_adm1()
    return gdf[["GID_1", "NAME_1"]].rename(columns={"GID_1": "state_id", "NAME_1": "name"}).to_dict(orient="records")


@router.get("/states/{state_id}/districts")
def list_districts_for_state(state_id: str) -> Sequence[dict]:
    """Find all the districts that belong to a specific state or union territory."""
    adm2 = get_adm2()
    state_districts = adm2[adm2["GID_1"] == state_id]
    if state_districts.empty:
        raise HTTPException(status_code=404, detail="State not found")
    return state_districts[["GID_2", "NAME_2"]].rename(
        columns={"GID_2": "district_id", "NAME_2": "name"}
    ).to_dict(orient="records")


@router.get("/states/{state_id}/districts/geojson")
def get_state_districts_geojson(state_id: str) -> dict:
    """Get the geographic shapes of all districts in a state, formatted as a GeoJSON FeatureCollection."""
    adm2 = get_adm2()
    state_districts = adm2[adm2["GID_1"] == state_id]
    if state_districts.empty:
        raise HTTPException(status_code=404, detail="State not found")

    gdf = state_districts[["GID_2", "NAME_2", "GID_1", "NAME_1", "geometry"]].rename(
        columns={
            "GID_2": "district_id",
            "NAME_2": "district_name",
            "GID_1": "state_id",
            "NAME_1": "state_name",
        }
    )
    return json.loads(gdf.to_json())
