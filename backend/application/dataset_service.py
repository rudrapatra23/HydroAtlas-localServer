from __future__ import annotations

import hashlib
import zipfile
from datetime import datetime, UTC
from pathlib import Path
from typing import BinaryIO, Optional

import xarray as xr

from application.dto.requests import DownloadRequest
from application.dto.responses import DownloadResponse
from application.providers.provider import Provider
from domain.entities.climate_asset import ClimateAsset, ClimateAssetStatus
from domain.ports.dataset_repository import DatasetRepository
from domain.ports.storage_port import StoragePort


class DatasetService:
    def __init__(self, repository: DatasetRepository, storage: StoragePort, provider: Optional[Provider] = None):
        self.repository = repository
        self.storage = storage
        self.provider = provider

    def _validate_netcdf(self, file_path: Path) -> Path:
        """Validate that the file is a valid NetCDF file using xarray. 
        If file is a ZIP archive, extract and return the inner NetCDF path.
        Returns the path to the valid NetCDF file (may be original or extracted)."""
        try:
            # Check if it's a ZIP file
            if zipfile.is_zipfile(file_path):
                with zipfile.ZipFile(file_path, 'r') as zf:
                    # Find the first .nc file in the archive
                    nc_names = [n for n in zf.namelist() if n.endswith('.nc')]
                    if not nc_names:
                        raise ValueError("ZIP archive contains no NetCDF files")
                    
                    # Extract to same directory as the zip file
                    extract_dir = file_path.parent
                    zf.extract(nc_names[0], extract_dir)
                    
                    # Return path to extracted file
                    extracted_path = extract_dir / nc_names[0]
                    # Validate the extracted file
                    with xr.open_dataset(extracted_path, engine="netcdf4") as ds:
                        ds.close()
                    return extracted_path
            
            # Regular NetCDF file
            with xr.open_dataset(file_path, engine="netcdf4") as ds:
                ds.close()
            return file_path
        except Exception as e:
            raise ValueError(f"Invalid NetCDF file: {e}")

    def _compute_checksum(self, file_path: Path) -> str:
        """Compute MD5 checksum of the file."""
        md5_hash = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()

    async def download_and_register(
        self,
        request: DownloadRequest,
    ) -> DownloadResponse:
        if not self.provider:
            raise ValueError("No provider configured for DatasetService")

        existing_asset = await self.repository.get_by_period(
            year=request.year,
            month=request.month,
            provider=request.provider,
            variable=request.variable,
        )
        if existing_asset:
            return DownloadResponse(
                success=True,
                storage_key=existing_asset.storage_key,
                provider=request.provider,
                variable=request.variable,
                year=request.year,
                month=request.month,
                file_path=None,
                checksum=existing_asset.checksum,
                file_size=existing_asset.file_size,
                error_message=None,
            )

        download_result = await self.provider.download(request)
        if not download_result.success or not download_result.file_path:
            return download_result

        downloaded_path = download_result.file_path
        temp_dir = downloaded_path.parent
        nc_path = None
        should_cleanup = False

        try:
            # Validate and extract if needed - returns path to NetCDF file
            nc_path = self._validate_netcdf(downloaded_path)
            
            # Track whether we extracted (need cleanup)
            should_cleanup = nc_path != downloaded_path

            # Compute checksum from the actual NetCDF file
            computed_checksum = self._compute_checksum(nc_path)

            storage_key = f"{request.provider}/{request.variable}/{request.year}/{request.month:02d}.nc"
            
            # Upload the NetCDF file (not the ZIP) to S3
            self.storage.upload(storage_key, nc_path)

            # Verify upload to S3
            if not self.storage.exists(storage_key):
                raise RuntimeError(f"File upload verification failed: {storage_key}")

            file_size = nc_path.stat().st_size

            asset = ClimateAsset(
                id=None,
                provider=request.provider,
                variable=request.variable,
                year=request.year,
                month=request.month,
                storage_key=storage_key,
                checksum=computed_checksum,
                file_size=file_size,
                status=ClimateAssetStatus.COMPLETED,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            saved_asset = await self.repository.save(asset)

            return DownloadResponse(
                success=True,
                storage_key=storage_key,
                provider=request.provider,
                variable=request.variable,
                year=request.year,
                month=request.month,
                file_path=nc_path,
                checksum=saved_asset.checksum,
                file_size=saved_asset.file_size,
                error_message=None,
            )
        finally:
            # Clean up temporary files
            if should_cleanup and nc_path:
                try:
                    # Remove extracted NetCDF file
                    if nc_path.exists():
                        nc_path.unlink()
                    # Remove parent directory of extraction if empty
                    extracted_dir = nc_path.parent
                    if extracted_dir.exists() and not any(extracted_dir.iterdir()):
                        extracted_dir.rmdir()
                except Exception:
                    pass  # Best effort cleanup
            
            # Always try to remove the original ZIP if it exists
            try:
                if downloaded_path.exists():
                    downloaded_path.unlink()
            except Exception:
                pass

    async def register_asset(
        self,
        provider: str,
        variable: str,
        year: int,
        month: int,
        file_path: Path | BinaryIO,
        file_size: int,
        checksum: str,
    ) -> ClimateAsset:
        storage_key = f"{provider}/{variable}/{year}/{month:02d}.nc"
        
        if self.storage.exists(storage_key):
            raise ValueError(f"Asset already exists in storage: {storage_key}")

        if await self.repository.exists(provider, variable, year, month):
            raise ValueError(f"Asset already registered: {provider}/{variable}/{year}/{month:02d}")

        self.storage.upload(storage_key, file_path)

        asset = ClimateAsset(
            id=None,
            provider=provider,
            variable=variable,
            year=year,
            month=month,
            storage_key=storage_key,
            checksum=checksum,
            file_size=file_size,
            status=ClimateAssetStatus.COMPLETED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        return await self.repository.save(asset)

    async def get_asset(self, asset_id: str) -> ClimateAsset | None:
        return await self.repository.get_by_id(asset_id)

    async def list_assets(self) -> Sequence[ClimateAsset]:
        return await self.repository.list()

    async def delete_asset(self, asset_id: str) -> None:
        asset = await self.repository.get_by_id(asset_id)
        if asset:
            await self.repository.delete(asset_id)
            self.storage.delete(asset.storage_key)
