from __future__ import annotations

from fastapi import FastAPI

from api.routers import health, datasets
from core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
    )

    app.include_router(health.router)
    app.include_router(datasets.router)

    return app


app = create_app()
