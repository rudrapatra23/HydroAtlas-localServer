
from fastapi.testclient import TestClient
import pytest

from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_list_states(client):
    """Test that get /boundaries/states returns list of states."""
    response = client.get("/boundaries/states")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 41
    assert all("state_id" in state and "name" in state for state in data)
    assert any(state["state_id"] == "IND.1_1" for state in data)
    assert any(state["name"] == "Andaman and Nicobar" for state in data)


def test_list_districts_for_state(client):
    response = client.get("/boundaries/states/IND.1_1/districts")
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    assert all("district_id" in d and "name" in d for d in data)
    assert any(d["name"] == "Nicobar Islands" for d in data)


def test_state_districts_geojson(client):
    districts_response = client.get("/boundaries/states/IND.1_1/districts")
    assert districts_response.status_code == 200
    districts = districts_response.json()

    geojson_response = client.get("/boundaries/states/IND.1_1/districts/geojson")
    assert geojson_response.status_code == 200
    geojson = geojson_response.json()

    assert geojson["type"] == "FeatureCollection"
    assert "features" in geojson
    assert len(geojson["features"]) == len(districts)
    assert all(f["properties"]["state_id"] == "IND.1_1" for f in geojson["features"])
