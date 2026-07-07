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

from fastapi import FastAPI  # noqa: E402  (import after diagnostics)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from api.routers import health, datasets, boundaries, districts, states  # noqa: E402
from core.config import Settings, get_settings  # noqa: E402


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
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
