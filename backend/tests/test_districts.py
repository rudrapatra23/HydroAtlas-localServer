from fastapi.testclient import TestClient
import pytest

from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_district_statistics_returns_expected_fields(client):
    """Test that POST /districts/{district_id}/statistics returns expected response structure."""
    response = client.post(
        "/districts/IND.1.1_1/statistics",
        json={
            "year": 2024,
            "month": 1,
            "variable": "precipitation"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["district_id"] == "IND.1.1_1"
    assert data["variable"] == "precipitation"
    assert "mean" in data
    assert "min" in data
    assert "max" in data
    assert isinstance(data["mean"], float)
    assert isinstance(data["min"], float)
    assert isinstance(data["max"], float)


def test_district_statistics_not_found(client):
    """Test that invalid district returns 404."""
    response = client.post(
        "/districts/INVALID.DISTRICT/statistics",
        json={
            "year": 2024,
            "month": 1,
            "variable": "precipitation"
        }
    )

    assert response.status_code == 404
