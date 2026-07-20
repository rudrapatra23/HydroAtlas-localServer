"""Post-migration verification."""

import ast
import importlib
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main() -> int:
    errors = 0

    # --- 1. Deleted files must not exist ---
    deleted = [
        "cli.py",
        "verify_clipping.py",
        "application/providers/era5_provider.py",
        "application/providers/provider.py",
        "application/providers/__init__.py",
    ]
    for rel in deleted:
        p = BACKEND / rel
        if p.exists():
            print(f"FAIL  {rel} still exists")
            errors += 1
        else:
            print(f"PASS  {rel} deleted")

    if (BACKEND / "application" / "providers").exists():
        print("FAIL  application/providers/ directory still exists")
        errors += 1
    else:
        print("PASS  application/providers/ directory deleted")

    # --- 2. Ingestion DTOs must not exist ---
    src_requests = (BACKEND / "application" / "dto" / "requests.py").read_text()
    for cls in ["BootstrapRequest", "DownloadRequest"]:
        if cls in src_requests:
            print(f"FAIL  {cls} still in requests.py")
            errors += 1
        else:
            print(f"PASS  {cls} removed from requests.py")

    src_responses = (BACKEND / "application" / "dto" / "responses.py").read_text()
    if "DownloadResponse" in src_responses:
        print("FAIL  DownloadResponse still in responses.py")
        errors += 1
    else:
        print("PASS  DownloadResponse removed from responses.py")

    # --- 3. DatasetService must be read-only (no download_and_register, no register_asset, no provider) ---
    src_service = (BACKEND / "application" / "dataset_service.py").read_text()
    tree = ast.parse(src_service)
    method_names = [
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    for removed in ["download_and_register", "register_asset", "_validate_netcdf", "_compute_checksum"]:
        if removed in method_names:
            print(f"FAIL  DatasetService still has method '{removed}'")
            errors += 1
        else:
            print(f"PASS  DatasetService method '{removed}' removed")

    for kept in ["get_asset", "list_assets", "delete_asset"]:
        if kept in method_names:
            print(f"PASS  DatasetService method '{kept}' preserved")
        else:
            print(f"FAIL  DatasetService method '{kept}' missing!")
            errors += 1

    if "provider" in src_service.lower().split("def __init__")[0] if "def __init__" in src_service else "":
        print("FAIL  DatasetService still references 'provider'")
        errors += 1

    if "Provider" in src_service:
        print("FAIL  DatasetService still imports Provider")
        errors += 1
    else:
        print("PASS  DatasetService has no Provider reference")

    # --- 4. delete_asset must NOT delete from S3 ---
    if "storage.delete" in src_service or "self.storage" in src_service:
        print("FAIL  DatasetService still calls storage.delete (S3 owned by era5_fetch)")
        errors += 1
    else:
        print("PASS  delete_asset does not touch S3")

    # --- 5. datasets.py router must not have POST /download ---
    src_router = (BACKEND / "api" / "routers" / "datasets.py").read_text()
    if "/download" in src_router or "download_dataset" in src_router:
        print("FAIL  datasets.py still has /download endpoint")
        errors += 1
    else:
        print("PASS  /download endpoint removed from datasets.py")

    for kept in ["list_datasets", "get_dataset", "delete_dataset"]:
        if kept in src_router:
            print(f"PASS  datasets.py endpoint '{kept}' preserved")
        else:
            print(f"FAIL  datasets.py endpoint '{kept}' missing!")
            errors += 1

    # --- 6. dependencies.py must not reference ERA5Provider ---
    src_deps = (BACKEND / "api" / "dependencies.py").read_text()
    if "ERA5Provider" in src_deps or "get_provider" in src_deps:
        print("FAIL  dependencies.py still references ERA5Provider/get_provider")
        errors += 1
    else:
        print("PASS  dependencies.py has no provider references")

    # --- 7. Analytics pipeline files must be untouched ---
    analytics_files = [
        "application/raster_computation.py",
        "api/routers/districts.py",
        "api/routers/states.py",
        "api/routers/boundaries.py",
        "api/routers/health.py",
        "domain/entities/climate_asset.py",
        "domain/ports/dataset_repository.py",
        "domain/ports/storage_port.py",
        "infrastructure/repositories/postgres_dataset_repository.py",
        "infrastructure/storage/s3_storage_adapter.py",
    ]
    for rel in analytics_files:
        p = BACKEND / rel
        if p.exists():
            print(f"PASS  {rel} exists (analytics intact)")
        else:
            print(f"FAIL  {rel} missing!")
            errors += 1

    # --- 8. Raster computation VARIABLE_MAP and variable pipeline intact ---
    src_raster = (BACKEND / "application" / "raster_computation.py").read_text()
    for var in ["precipitation", "soil_moisture", "surface_runoff"]:
        if f'"{var}"' in src_raster:
            print(f"PASS  VARIABLE_MAP contains '{var}'")
        else:
            print(f"FAIL  VARIABLE_MAP missing '{var}'")
            errors += 1

    if "xr.open_dataset" in src_raster:
        print("PASS  raster_computation uses xr.open_dataset")
    else:
        print("FAIL  raster_computation missing xr.open_dataset")
        errors += 1

    # --- 9. Key imports resolve ---
    try:
        from application.dataset_service import DatasetService
        from application.dto.requests import StatisticsRequest
        from application.dto.responses import (
            ClimateAssetResponse, StatisticsResponse,
            StateDistrictStatisticsItem, StateDistrictStatisticsResponse,
        )
        from api.dependencies import get_dataset_service, get_repository, get_storage
        print("PASS  All production imports resolve")
    except ImportError as e:
        print(f"FAIL  Import error: {e}")
        errors += 1

    # --- 10. No stale ingestion imports in production code ---
    prod_files = list((BACKEND / "api").rglob("*.py")) + list((BACKEND / "application").rglob("*.py"))
    stale_refs = ["DownloadRequest", "DownloadResponse", "BootstrapRequest", "ERA5Provider", "from application.providers"]
    for pf in prod_files:
        if "__pycache__" in str(pf):
            continue
        content = pf.read_text()
        for ref in stale_refs:
            if ref in content:
                print(f"FAIL  {pf.relative_to(BACKEND)} still references '{ref}'")
                errors += 1

    print()
    if errors == 0:
        print("ALL CHECKS PASSED — Migration complete")
    else:
        print(f"{errors} CHECK(S) FAILED")
    return errors


if __name__ == "__main__":
    sys.exit(main())
