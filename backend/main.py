from __future__ import annotations

# Diagnostics must be installed BEFORE any native library import.
# xarray, netCDF4, rasterio are imported lazily by the request path,
# so installing faulthandler here covers the entire process lifetime.
from application.diagnostics import setup_diagnostics
setup_diagnostics()

# Activate the persistent NATIVE_* lifecycle sink as early as possible
# so crash-adjacent lines survive 0xC0000005. Default path is
# backend/native_lifecycle.log; flush+fsync per line; failures are
# swallowed inside the helper. No behavioral change.
from application.diagnostics import setup_lifecycle_log
setup_lifecycle_log()

import logging  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from pathlib import Path  # noqa: E402

from fastapi import FastAPI  # noqa: E402  (import after diagnostics)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from api.routers import health, datasets, boundaries, districts, states  # noqa: E402
from core.config import Settings, get_settings  # noqa: E402
from infrastructure.geospatial.boundary_loader import _GADM_PATH  # noqa: E402

logger = logging.getLogger("uvicorn.error")


def _validate_gadm_on_disk() -> None:
    """Log the resolved GADM path on startup; warn loudly if missing.

    We deliberately do not raise here so a missing GADM file does not
    crash the whole process — the rest of HydroAtlas (datasets,
    health, etc.) is still useful for ops. Instead we emit a loud
    WARNING that surfaces in ``uvicorn.error`` and the operator
    dashboard. The first request to a district endpoint will fail
    with a clear FileNotFoundError from the boundary loader.
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
    """Application lifespan: validate data dependencies on startup.

    Kept lightweight — we never block startup on a real S3 download.
    The first request to ``/districts/{id}/raster-clip`` will fail
    with a clear 404/503 message if the underlying NetCDF cannot be
    fetched; doing that lazily keeps the process responsive even when
    S3 is briefly unreachable.
    """
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
