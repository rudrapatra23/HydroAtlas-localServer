from __future__ import annotations

# Wire up diagnostics before anything else pulls in xarray/netCDF4/rasterio.
# Those native libs really need to be instrumented from the start, otherwise
# we miss any segfaults or weird memory stuff that happens on import.
from application.diagnostics import setup_diagnostics
setup_diagnostics()

# Lifecycle log goes in early for the same reason — if a native lib crashes
# hard, we still want some breadcrumbs in the log to figure out what happened.
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
    """Make sure the gadm boundary file is actually on disk at boot."""
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
    """Run startup/shutdown checks without blocking on anything remote."""
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
    # Note to self: if raster-clip-range ever gets pulled out into its own
    # router (probably under district_clip), don't forget to mount it here.

    return app


app = create_app()
