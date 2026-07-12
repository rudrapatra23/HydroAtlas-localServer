from __future__ import annotations

# Diagnostics must be installed before any native library import (xarray,
# netCDF4, rasterio are imported lazily by the request path).
from application.diagnostics import setup_diagnostics
setup_diagnostics()

# Persistent NATIVE_* lifecycle sink, active early so crash-adjacent lines
# survive 0xC0000005. Defaults to backend/native_lifecycle.log.
from application.diagnostics import setup_lifecycle_log
setup_lifecycle_log()

import logging  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from api.routers import health, datasets, boundaries, districts, states  # noqa: E402
from core.config import Settings, get_settings  # noqa: E402
from infrastructure.geospatial.boundary_loader import _GADM_PATH  # noqa: E402

logger = logging.getLogger("uvicorn.error")


def _validate_gadm_on_disk() -> None:
    """Log the resolved GADM path on startup; warn loudly if missing.

    Doesn't raise — a missing GADM file shouldn't crash the whole process,
    since health/datasets endpoints are still useful. District-level
    endpoints will fail with a clear FileNotFoundError on first request.
    """
    gadm_path = Path(_GADM_PATH)
    if not gadm_path.exists():
        logger.warning(
            "STARTUP_CHECK GADM file MISSING path=%s size_bytes=0. "
            "District-level endpoints (boundaries, districts statistics, "
            "district_raster_clip) will fail until this file is restored.",
            gadm_path,
        )
        return
    size_mb = gadm_path.stat().st_size / (1024 * 1024)
    logger.info(
        "STARTUP_CHECK GADM OK path=%s size_mb=%.1f",
        gadm_path, size_mb,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate data dependencies on startup without blocking on S3."""
    logger.info("STARTUP_BEGIN app=%s version=%s",
                app.title, app.version)
    _validate_gadm_on_disk()
    logger.info("STARTUP_DONE")
    yield
    logger.info("SHUTDOWN_BEGIN")
    logger.info("SHUTDOWN_DONE")


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
            "http://hydroatlas-frontend-alb-322053000.ap-south-1.elb.amazonaws.com",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(datasets.router)
    app.include_router(boundaries.router)
    app.include_router(districts.router)
    app.include_router(states.router)

    return app


app = create_app()