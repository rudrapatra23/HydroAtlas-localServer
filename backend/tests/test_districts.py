from fastapi.testclient import TestClient
import pytest

from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_district_statistics_returns_expected_fields(client):
    """Test that post /districts/{district_id}/statistics accepts an inclusive."""
    response = client.post(
        "/districts/IND.1.1_1/statistics",
        json={
            "start_year": 2024,
            "start_month": 1,
            "end_year": 2024,
            "end_month": 3,
            "variable": "precipitation",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["district_id"] == "IND.1.1_1"
    assert data["variable"] == "precipitation"
    assert data["start_year"] == 2024
    assert data["start_month"] == 1
    assert data["end_year"] == 2024
    assert data["end_month"] == 3
    assert "months_processed" in data
    assert isinstance(data["months_processed"], int)
    assert "mean" in data
    assert "min" in data
    assert "max" in data
    assert isinstance(data["mean"], float)
    assert isinstance(data["min"], float)
    assert isinstance(data["max"], float)


def test_district_statistics_rejects_inverted_range(client):
    """Test that a start month greater than the end month returns 400."""
    response = client.post(
        "/districts/IND.1.1_1/statistics",
        json={
            "start_year": 2024,
            "start_month": 6,
            "end_year": 2024,
            "end_month": 1,
            "variable": "precipitation",
        },
    )

    assert response.status_code == 400


def test_district_statistics_not_found(client):
    """Test that an invalid district returns 404."""
    response = client.post(
        "/districts/INVALID.DISTRICT/statistics",
        json={
            "start_year": 2024,
            "start_month": 1,
            "end_year": 2024,
            "end_month": 3,
            "variable": "precipitation",
        },
    )

    assert response.status_code == 404
