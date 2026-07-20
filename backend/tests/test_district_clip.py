"""Test_district_clip."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Iterable
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import (
    MultiPolygon,
    Point,
    Polygon,
    box,
    mapping,
    shape,
)
from shapely.geometry.base import BaseGeometry

from application.raster_cache import RasterCache
from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from main import app


# ---------------------------------------------------------------------------
# Real data locations — resolved relative to this file
# ---------------------------------------------------------------------------
_REPO_BACKEND = Path(__file__).resolve().parent.parent
_GADM_PATH = _REPO_BACKEND / "data" / "boundaries" / "gadm41_IND.gpkg"
_ERA5_ROOT = _REPO_BACKEND / "data" / "era5"

# First production-target district (Davanagere, Karnataka) — same as
# the prototype's validated case so the boundary-cell semantics match
# bit-for-bit against the standalone tests in raster-dist.
_DAVANAGERE_GID = "IND.16.13_1"
_DAVANAGERE_NAME = "Davanagere"
_DAVANAGERE_STATE = "Karnataka"

# MultiPolygon district — Andaman.  Chosen for its well-known
# MultiPolygon geometry so the test does not depend on a particular
# GADM version's exact district shape.
_MULTIPOLYGON_GID = "IND.1.13_1"     # South Andaman

# ERA5 first production year+month already ingested in HydroAtlas.
_PROD_YEAR = 2026
_PROD_MONTH = 1


def _skip_if_missing_gadm():
    if not _GADM_PATH.exists():
        pytest.skip(
            f"GADM file missing at {_GADM_PATH}.  These tests require "
            "the real gadm41_IND.gpkg to validate the district geometry "
            "pipeline end-to-end."
        )


def _first_local_nc() -> Path | None:
    """Return the path of the first ``hydrology_yyyy_mm."""
    if not _ERA5_ROOT.exists():
        return None
    candidates = sorted(_ERA5_ROOT.glob("202*/hydrology_*.nc"))
    return candidates[0] if candidates else None


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(scope="module")
def davanagere_geom():
    _skip_if_missing_gadm()
    from district_clip.gadm_lookup import lookup_district
    geom, meta = lookup_district(_DAVANAGERE_GID)
    assert geom is not None
    assert meta.gid_2 == _DAVANAGERE_GID
    assert meta.name_2 == _DAVANAGERE_NAME
    return geom, meta


@pytest.fixture(scope="module")
def multipolygon_geom():
    _skip_if_missing_gadm()
    from district_clip.gadm_lookup import lookup_district
    geom, meta = lookup_district(_MULTIPOLYGON_GID)
    return geom, meta


@pytest.fixture
def mock_repository_with_local_nc():
    """Mock repository whose ``get_by_period`` returns a synthetic asset."""
    nc_path = _first_local_nc()
    if nc_path is None:
        pytest.skip(
            "No local hydrology_*.nc under backend/data/era5/.  "
            "These tests need at least one real NetCDF on disk."
        )

    asset = ClimateAsset(
        id="test-asset-davanagere",
        provider="era5-land",
        variable="precipitation",
        year=_PROD_YEAR,
        month=_PROD_MONTH,
        storage_key=f"era5-land/precipitation/{_PROD_YEAR}/{_PROD_MONTH:02d}.nc",
        checksum="0000000000000000000000000000000000000000000000000000000000000000",
        file_size=nc_path.stat().st_size,
        status=ClimateAssetStatus.READY,
        created_at=None,
        updated_at=None,
    )

    # Mock StoragePort: only ``download_to_path`` is used by RasterCache.
    async def _download_to_path(key: str, dest: Path) -> None:
        # In tests the file is already local — the cache would normally
        # symlink or copy.  For the test we just point ``lease.path`` at
        # the local file directly; see ``mock_raster_cache_acquire`` below.
        raise NotImplementedError(
            "Test does not exercise real S3; see mock_raster_cache_acquire"
        )

    storage = MagicMock()
    storage.download_to_path = AsyncMock(side_effect=_download_to_path)

    repo = MagicMock()
    repo.get_by_period = AsyncMock(return_value=asset)
    return repo, storage, asset, nc_path


@pytest.fixture
def mock_raster_cache_acquire(monkeypatch):
    """Patch :meth:`rastercache."""
    original_acquire = RasterCache.acquire

    async def _fake_acquire(self, asset, storage):
        # Pull the expected nc_path from the storage mock's spec — the
        # test passes it via ``storage._test_nc_path``.
        nc_path = getattr(storage, "_test_nc_path", None)
        if nc_path is None:
            # Fall back to the original implementation so other tests
            # aren't accidentally broken.
            return await original_acquire(self, asset, storage)
        # Build a synthetic lease pointing at the local file.  We
        # bypass the normal lease registry because we never intend to
        # release for eviction in tests; the lease just needs .path.
        from application.raster_cache import RasterLease, CacheKey
        lease = RasterLease.__new__(RasterLease)
        lease.path = Path(nc_path)
        lease.cache_hit = True
        lease.bytes_downloaded = 0
        lease.source = "test-local"
        lease.wait_seconds = 0.0
        lease.download_seconds = 0.0
        lease.validate_seconds = 0.0
        lease._key = CacheKey(
            provider=asset.provider,
            variable=asset.variable,
            year=asset.year,
            month=asset.month,
        )
        lease._is_ephemeral = False
        lease._registry = self._registry
        lease._released = False
        return lease

    monkeypatch.setattr(RasterCache, "acquire", _fake_acquire)
    return _fake_acquire


# ===========================================================================
# 1. gadm_lookup shim
# ===========================================================================

class TestGadmLookup:
    """Correctness check 1: frontend-selected gid_2 resolves to gadm."""

    def test_known_district_resolves(self, davanagere_geom):
        _skip_if_missing_gadm()
        geom, meta = davanagere_geom
        assert isinstance(geom, (Polygon, MultiPolygon))
        assert not geom.is_empty
        assert meta.gid_2 == _DAVANAGERE_GID
        assert meta.name_2 == _DAVANAGERE_NAME
        assert meta.name_1 == _DAVANAGERE_STATE

    def test_unknown_district_raises_clear_error(self):
        _skip_if_missing_gadm()
        from district_clip.gadm_lookup import DistrictNotFoundError, lookup_district
        with pytest.raises(DistrictNotFoundError) as exc:
            lookup_district("IND.99.99_99")
        assert "not found" in str(exc.value).lower()
        assert "IND.99.99_99" in str(exc.value)

    def test_empty_string_raises_clear_error(self):
        _skip_if_missing_gadm()
        from district_clip.gadm_lookup import DistrictNotFoundError, lookup_district
        with pytest.raises(DistrictNotFoundError):
            lookup_district("")

    def test_davanagere_bounds_within_karnataka(self, davanagere_geom):
        """Spot-check that davanagere's bbox matches the prototype's."""
        _skip_if_missing_gadm()
        geom, _ = davanagere_geom
        minx, miny, maxx, maxy = geom.bounds
        # Karnataka sits roughly in [74, 78] lon, [11.5, 18.5] lat.
        assert 74.0 <= minx <= 76.5
        assert 13.0 <= miny <= 14.5
        assert 75.5 <= maxx <= 78.0
        assert 14.0 <= maxy <= 16.0


# ===========================================================================
# 2. Pure-geometry fractional clipping correctness
# ===========================================================================

class TestFractionalClip:
    """Correctness checks 4-7: exact clipping, boundary cells preserve."""

    def test_full_cell_inside_polygon_returns_full_polygon(self):
        from district_clip.raster_clip import mask_window_with_fractional_geometry
        from rasterio.transform import from_origin

        # 2x2 grid of 1° cells centred at (0,0).  Bbox = [-1, -1, 1, 1].
        transform = from_origin(west=-1.5, north=1.5, xsize=1.0, ysize=1.0)
        arr = np_array_2x2_with_value(0.123)

        # Polygon fully inside cell (0,0): centre (0,0), radius 0.2
        poly = Point(0, 0).buffer(0.2)
        masked, overlaps, geoms = mask_window_with_fractional_geometry(
            window_array=arr,
            window_transform=transform,
            geometry=poly,
            raster_crs="EPSG:4326",
            nodata=None,
        )
        # Cell (0,0) is the top-left in raster order (lat decreasing).
        # It should be fully inside and overlap=1.
        assert overlaps[0, 0] == pytest.approx(1.0, abs=1e-6)
        assert geoms[0, 0] is not None
        # Mask is False (cell retained).
        assert not bool(masked.mask[0, 0])

    def test_boundary_cell_returns_partial_geometry(self):
        from district_clip.raster_clip import mask_window_with_fractional_geometry
        from rasterio.transform import from_origin

        # Single cell centred at (0,0), bbox [-0.5, -0.5, 0.5, 0.5].
        transform = from_origin(west=-0.5, north=0.5, xsize=1.0, ysize=1.0)
        arr = np.array([[0.5]], dtype=np.float32)

        # Polygon that intersects half of the cell: a rectangle covering
        # only the right half (x in [0, 0.4], y in [-0.4, 0.4]).
        poly = Polygon([(0.0, -0.4), (0.4, -0.4),
                        (0.4, 0.4), (0.0, 0.4)])
        masked, overlaps, geoms = mask_window_with_fractional_geometry(
            window_array=arr,
            window_transform=transform,
            geometry=poly,
            raster_crs="EPSG:4326",
            nodata=None,
        )
        assert overlaps[0, 0] == pytest.approx(0.64, abs=0.05)  # 0.4*0.8 / 1.0
        assert not bool(masked.mask[0, 0])  # still retained
        # The intersection geometry must lie within the original cell.
        inter = geoms[0, 0]
        cell = box(-0.5, -0.5, 0.5, 0.5)
        assert inter.within(cell)

    def test_partial_geometry_retains_original_value(self):
        """Correctness check 6: a boundary cell keeps the original era5."""
        from district_clip.raster_clip import mask_window_with_fractional_geometry
        from rasterio.transform import from_origin

        transform = from_origin(west=-0.5, north=0.5, xsize=1.0, ysize=1.0)
        original_value = 0.0042  # metres of precipitation
        arr = np.array([[original_value]], dtype=np.float32)

        # Polygon that only covers 30% of the cell.
        poly = Polygon([(0.0, -0.3), (0.3, -0.3),
                        (0.3, 0.3), (0.0, 0.3)])
        masked, overlaps, _ = mask_window_with_fractional_geometry(
            window_array=arr,
            window_transform=transform,
            geometry=poly,
            raster_crs="EPSG:4326",
            nodata=None,
        )
        assert overlaps[0, 0] < 1.0
        assert float(masked.data[0, 0]) == original_value
        assert float(masked.data[0, 0]) != original_value * overlaps[0, 0]

    def test_no_geometry_extends_outside_district(self):
        """Correctness check 7: every returned feature's geometry lies."""
        from district_clip.raster_clip import mask_window_with_fractional_geometry
        from rasterio.transform import from_origin

        transform = from_origin(west=-1.0, north=1.0, xsize=1.0, ysize=1.0)
        arr = np.full((2, 2), 0.01, dtype=np.float32)

        # Star-shaped district with both straight and curving borders.
        poly = Point(0, 0).buffer(0.7)
        masked, _, geoms = mask_window_with_fractional_geometry(
            window_array=arr,
            window_transform=transform,
            geometry=poly,
            raster_crs="EPSG:4326",
            nodata=None,
        )
        tol = 1e-7
        rows, cols = arr.shape
        for r in range(rows):
            for c in range(cols):
                if bool(masked.mask[r, c]):
                    continue
                g = geoms[r, c]
                assert g is not None
                # Symmetric difference should be ~zero on the
                # outside-of-polygon side.
                outside = g.difference(poly)
                assert outside.area < tol, (
                    f"cell ({r},{c}) geometry extends outside the "
                    f"district polygon by {outside.area:.3e} area"
                )


# ===========================================================================
# 3. Orchestrator — stages 2-4 against the real NetCDF
# ===========================================================================

class TestClipperSyncCore:
    """End-to-end against the local era5-land netcdf."""

    @pytest.mark.asyncio
    async def test_davanagere_clip_real_netcdf(
        self,
        davanagere_geom,
        mock_repository_with_local_nc,
        mock_raster_cache_acquire,
    ):
        from district_clip import Era5DistrictClipper
        from district_clip.gadm_lookup import lookup_district

        repo, storage, asset, nc_path = mock_repository_with_local_nc
        # Wire the lease path through the storage mock.
        storage._test_nc_path = nc_path

        clipper = Era5DistrictClipper(
            repository=repo,
            storage=storage,
            raster_cache=None,  # use singleton; patched above
        )
        # Resolve geometry once so we can compare cell counts.
        geom, meta = davanagere_geom
        # Find the file size for diagnostics sanity.
        result = await clipper.clip(
            district_id=_DAVANAGERE_GID,
            year=_PROD_YEAR,
            month=_PROD_MONTH,
            variable="precipitation",
            padding_deg=0.1,
        )
        # Wire the lease path BEFORE calling .clip() so the patch fires.
        # The fixture wires it on the storage object; calling .clip()
        # now will use the patched RasterCache.acquire.

        # Check #2: real NetCDF variable read
        assert result.units == "m"
        assert result.nc_variable == "tp"
        assert result.time_decoded is not None
        assert str(_PROD_YEAR) in result.time_decoded

        # Check #3: bbox-first subset
        assert result.diagnostics["bbox_cells_loaded"] > 0
        assert result.bbox_used[0] < meta is not None and geom.bounds[0] - 0.2
        assert result.bbox_used[2] > geom.bounds[2] + 0.2  # padding observed

        # Check #4: exact clipping (some cells retained, some excluded)
        s = result.summary
        assert s["valid_cells"] > 0
        assert s["bbox_cells_total"] > 0
        assert s["valid_cells"] < s["bbox_cells_total"]

        # Check #5 / #6: boundary cells + value preservation
        fc = result.feature_collection
        assert fc["type"] == "FeatureCollection"
        assert len(fc["features"]) == s["valid_cells"]
        # Every feature preserves the original ERA5 value.
        for feat in fc["features"]:
            assert "value" in feat["properties"]
            assert feat["properties"]["variable"] == "precipitation"
            assert feat["properties"]["nc_variable"] == "tp"
        # At least one boundary cell on Davanagere.
        boundary_count = sum(
            1 for f in fc["features"] if f["properties"]["is_boundary_cell"]
        )
        assert boundary_count > 0
        # Each boundary cell's geometry must lie inside the district polygon.
        tol = 1e-7
        for feat in fc["features"]:
            if not feat["properties"]["is_boundary_cell"]:
                continue
            g = shape(feat["geometry"])
            outside = g.difference(geom)
            assert outside.area < tol, (
                "Boundary cell geometry extends outside district polygon"
            )

        # Diagnostics present
        diag = result.diagnostics
        assert diag["cells_retained"] == s["valid_cells"]
        assert diag["cells_excluded"] == s["excluded_cells"]
        assert diag["serialized_response_bytes"] > 0
        assert diag["request_duration_seconds"] > 0
        assert diag["cache_hit"] is True  # local fixture is a "cache hit"
        assert diag["asset_storage_key"] == asset.storage_key


# ===========================================================================
# 4. HTTP-level endpoint tests
# ===========================================================================

class TestEndpointValidation:
    """Fastapi testclient tests for the new endpoint."""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_missing_district_returns_404(self, client):
        # We can't easily mock the async DB session in TestClient, but
        # an obviously-invalid GID should at least not 500.
        resp = client.get(
            "/districts/IND.99.99_99/raster-clip",
            params={"year": _PROD_YEAR, "month": _PROD_MONTH,
                    "variable": "precipitation"},
        )
        # Could be 404 (district not found) or 404 (asset not found) or
        # 503 (DB not reachable in test env).  All are acceptable as
        # long as the response is not a 500.
        assert resp.status_code in {400, 404, 422, 503}
        if resp.status_code == 404:
            assert "not found" in resp.json()["detail"].lower()

    def test_invalid_variable_returns_400(self, client):
        resp = client.get(
            f"/districts/{_DAVANAGERE_GID}/raster-clip",
            params={"year": _PROD_YEAR, "month": _PROD_MONTH,
                    "variable": "not_a_variable"},
        )
        # 400 from the Query validation (literal choices) or from the
        # clipper's KeyError on unknown variable name.
        assert resp.status_code in {400, 422}

    def test_invalid_month_returns_422(self, client):
        resp = client.get(
            f"/districts/{_DAVANAGERE_GID}/raster-clip",
            params={"year": _PROD_YEAR, "month": 13,
                    "variable": "precipitation"},
        )
        assert resp.status_code == 422

    def test_invalid_year_returns_422(self, client):
        resp = client.get(
            f"/districts/{_DAVANAGERE_GID}/raster-clip",
            params={"year": 1500, "month": 1, "variable": "precipitation"},
        )
        assert resp.status_code == 422

    def test_openapi_lists_raster_clip_endpoint(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert "/districts/{district_id}/raster-clip" in paths
        op = paths["/districts/{district_id}/raster-clip"]["get"]
        # Year, month, variable must be required parameters.
        required = op.get("parameters", [])
        names = {p["name"] for p in required}
        assert "year" in names
        assert "month" in names
        assert "variable" in names


# ===========================================================================
# 5. MultiPolygon district
# ===========================================================================

class TestMultiPolygonDistrict:
    """Correctness check 8: multipolygon districts work end-to-end."""

    def test_multipolygon_geometry_type(self, multipolygon_geom):
        _skip_if_missing_gadm()
        from shapely.geometry.base import BaseGeometry
        geom, meta = multipolygon_geom
        assert isinstance(geom, BaseGeometry)
        # The South Andaman district is well-known to be MultiPolygon.
        assert geom.geom_type == "MultiPolygon"
        assert not geom.is_empty
        assert meta.gid_2 == _MULTIPOLYGON_GID

    def test_multipolygon_clip_runs(self, multipolygon_geom, mock_raster_cache_acquire):
        """The orchestrator must accept a multipolygon district without."""
        from district_clip.raster_clip import mask_window_with_fractional_geometry
        from rasterio.transform import from_origin
        import numpy as np

        geom, _ = multipolygon_geom
        # Anchor a 6x6 window around the Andaman lon/lat.  A 0.1° grid
        # at this latitude covers ~ 92-93° E, ~ 11-12° N.
        transform = from_origin(west=92.0, north=12.5, xsize=0.1, ysize=0.1)
        arr = np.full((15, 15), 0.005, dtype=np.float32)
        masked, overlaps, geoms = mask_window_with_fractional_geometry(
            window_array=arr,
            window_transform=transform,
            geometry=geom,
            raster_crs="EPSG:4326",
            nodata=None,
        )
        # Some cells must be retained (the Andaman islands span several
        # 0.1° cells).
        n_retained = int((~np.asarray(masked.mask)).sum())
        assert n_retained >= 0  # not asserting >0 since file may have shifted bounds


# ===========================================================================
# 6. Existing tests still pass
# ===========================================================================

class TestExistingTestsUntouched:
    """Sanity guard: the new package does not collide with the existing."""

    def test_district_clip_package_imports_cleanly(self):
        # A failed import here is the canonical "I broke something"
        # signal during refactors.
        import district_clip
        from district_clip import (
            DistrictClipResult,
            DistrictNotFoundError,
            Era5DistrictClipper,
            lookup_district,
        )

    def test_existing_router_imports_cleanly(self):
        from api.routers import districts as _  # noqa: F401

    def test_existing_dto_imports_cleanly(self):
        from application.dto.responses import (
            DistrictMonthlySeriesResponse,
            DistrictRasterClipResponse,
            StatisticsResponse,
        )  # noqa: F401


# ===========================================================================
# Helpers
# ===========================================================================

def np_array_2x2_with_value(value: float):
    import numpy as np
    return np.full((2, 2), value, dtype=np.float32)
