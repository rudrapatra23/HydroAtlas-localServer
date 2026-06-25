#!/usr/bin/env python3
"""Verify raster clipping is spatially correct."""
from __future__ import annotations

import asyncio
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rioxarray

from application.raster_computation import RasterComputation
from infrastructure.db.session import async_session_maker
from infrastructure.repositories.postgres_dataset_repository import PostgresDatasetRepository
from infrastructure.storage.s3_storage_adapter import S3StorageAdapter


async def verify_clipping(district_gid: str = "IND.1.1_1") -> Path:
    """Load raster, clip to district, save and visualize."""
    
    async with async_session_maker() as session:
        repo = PostgresDatasetRepository(session)
        storage = S3StorageAdapter()
        computation = RasterComputation(repo, storage)
        
        # Get district geometry
        geometry = computation.get_district_geometry(district_gid)
        district_name = geometry.iloc[0]["NAME_2"]
        state_name = geometry.iloc[0]["NAME_1"]
        
        print(f"District: {district_name}, {state_name}")
        print(f"District CRS: {geometry.crs}")
        
        # Get asset
        asset = await repo.get_by_period(2024, 1, "era5-land", "temperature")
        if not asset:
            raise ValueError("No asset found")
        
        # Load raster from S3
        raster, temp_path = computation.read_raster_from_s3(asset)
        print(f"Original raster CRS: {raster.rio.crs}")
        print(f"Original raster shape: {raster.rio.shape}")
        print(f"Original raster bounds: {raster.rio.bounds()}")
        
        # Clip raster
        clipped = raster.rio.clip(geometry.geometry.values, geometry.crs)
        print(f"Clipped raster CRS: {clipped.rio.crs}")
        print(f"Clipped raster shape: {clipped.rio.shape}")
        print(f"Clipped raster bounds: {clipped.rio.bounds()}")
        
        # Verify CRS match
        assert geometry.crs == clipped.rio.crs, "CRS mismatch!"
        print("CRS verification: PASSED")
        
        # Verify geometry alignment
        geom_bounds = geometry.total_bounds  # [minx, miny, maxx, maxy]
        raster_bounds = clipped.rio.bounds()
        print(f"Geometry bounds: {geom_bounds}")
        print(f"Clipped raster bounds: {raster_bounds}")
        
        # Check if raster is within geometry bounds
        with np.errstate(invalid='ignore'):
            # Get first data variable
            data_var = list(clipped.data_vars)[0]
            data = clipped[data_var]
            
            # Handle 3D array (band, y, x) - squeeze to 2D if needed
            if 'band' in data.dims:
                data_2d = data.squeeze('band').drop_vars('band')
            else:
                data_2d = data
            
            # Reload after drop
            data_2d = data_2d.load()
            
            # Save clipped raster (as 2D)
            output_dir = Path("clipped_output")
            output_dir.mkdir(exist_ok=True)
            clipped_path = output_dir / f"{district_gid}_clipped.tif"
            data_2d.rio.to_raster(str(clipped_path))
            print(f"Saved clipped raster to: {clipped_path}")
            
            # Create visualization
            fig, axes = plt.subplots(1, 3, figsize=(15, 5))
            
            # Plot 1: Original raster extent with district overlay
            ax1 = axes[0]
            ax1.set_title("Original Raster with District Boundary")
            
            # Plot clipped raster as background (use DataArray, not Dataset)
            data_2d.plot(ax=ax1, cmap="viridis", add_colorbar=False, alpha=0.7)
            
            # Overlay district boundary
            geometry.boundary.plot(ax=ax1, edgecolor='red', linewidth=2)
            ax1.set_xlabel("Longitude")
            ax1.set_ylabel("Latitude")
            
            # Plot 2: Clipped raster with district boundary
            ax2 = axes[1]
            ax2.set_title("Clipped Raster with District Mask")
            
            # Get 2D values for plotting
            values_2d = np.squeeze(data_2d.values)
            
            # Plot only valid data
            valid_mask = ~np.isnan(values_2d)
            masked_data = np.where(valid_mask, values_2d, np.nan)
            
            im = ax2.imshow(masked_data, cmap="viridis", extent=raster_bounds, origin='upper')
            geometry.boundary.plot(ax=ax2, edgecolor='red', linewidth=2)
            plt.colorbar(im, ax=ax2, label="Temperature")
            ax2.set_xlabel("Longitude")
            ax2.set_ylabel("Latitude")
            
            # Plot 3: Just the masked cells
            ax3 = axes[2]
            ax3.set_title("Masked Pixels (Valid Data Only)")
            
            # Show only valid pixel locations
            y_coords = np.arange(masked_data.shape[0])
            x_coords = np.arange(masked_data.shape[1])
            ax3.scatter(
                x_coords[np.newaxis, :] * np.ones_like(masked_data),
                y_coords[:, np.newaxis] * np.ones_like(masked_data),
                c=masked_data, cmap="viridis", s=50, marker='s', alpha=0.8
            )
            geometry.boundary.plot(ax=ax3, edgecolor='red', linewidth=2)
            ax3.set_xlim(raster_bounds[0], raster_bounds[2])
            ax3.set_ylim(raster_bounds[1], raster_bounds[3])
            ax3.set_xlabel("Longitude")
            ax3.set_ylabel("Latitude")
            
            plt.suptitle(f"Verification: {district_name}, {state_name} ({district_gid})")
            plt.tight_layout()
            
            fig_path = output_dir / f"{district_gid}_verification.png"
            plt.savefig(fig_path, dpi=150, bbox_inches='tight')
            plt.close()
            print(f"Saved verification plot to: {fig_path}")
            
            # Cleanup
            raster.close()
            try:
                temp_path.unlink()
            except PermissionError:
                pass
            
            return fig_path


if __name__ == "__main__":
    fig_path = asyncio.run(verify_clipping())
    print(f"\nVerification complete. Open: {fig_path}")
