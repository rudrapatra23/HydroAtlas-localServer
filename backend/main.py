"""FastAPI application entry point.

Exposes the application factory and a single liveness endpoint.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application.

    Args:
        settings: Optional pre-built settings instance. When ``None``,
            settings are loaded from the environment via
            :func:`app.core.config.get_settings`. Injecting a
            pre-built instance is useful for testing.

    Returns:
        A fully configured :class:`fastapi.FastAPI` application.
    """
    # Resolve settings exactly once per app instance. Falling back to
    # get_settings() preserves the production startup path while
    # allowing tests to inject a fixture.
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe.

        Returns:
            A JSON object with the service status and the deployed
            version. Used by orchestrators and uptime monitors.
        """
        return {"status": "healthy", "version": settings.version}

    return app


# Module-level ASGI application. ``uvicorn app.main:app`` binds here.
app = create_app()
