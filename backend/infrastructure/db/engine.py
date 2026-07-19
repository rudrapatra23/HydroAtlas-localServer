from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine

from core.config import get_settings


def get_engine() -> AsyncEngine:
    settings = get_settings()
    connect_args: dict[str, object] = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_async_engine(
        settings.database_url,
        echo=settings.environment == "development",
        connect_args=connect_args,
    )
