from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from urllib.parse import urlparse

from core.config import get_settings


def get_engine() -> AsyncEngine:
    settings = get_settings()
    
    # Parse the database URL
    parsed = urlparse(settings.database_url)
    
    # Build asyncpg-compatible URL with just scheme, user, password, host, port, database
    # Query parameters like sslmode, channel_binding are not compatible with asyncpg
    # We handle them via connect_args instead
    netloc = parsed.netloc
    if '@' in netloc:
        # Has credentials - extract them
        userinfo, hostinfo = netloc.rsplit('@', 1)
        if ':' in userinfo:
            user, password = userinfo.split(':', 1)
        else:
            user, password = userinfo, ''
        if ':' in hostinfo:
            host, port = hostinfo.split(':', 1)
        else:
            host, port = hostinfo, ''
    else:
        user, password, host, port = '', '', netloc, ''
    
    database = parsed.path.lstrip('/') if parsed.path else ''
    
    # Build URL without query parameters
    asyncpg_url = f"postgresql+asyncpg://"
    if user:
        asyncpg_url += f"{user}"
        if password:
            asyncpg_url += f":{password}"
        asyncpg_url += "@"
    asyncpg_url += host
    if port:
        asyncpg_url += f":{port}"
    if database:
        asyncpg_url += f"/{database}"
    
    # Determine SSL requirement from query params
    query_params = dict(param.split('=') for param in parsed.query.split('&') if '=' in param and param)
    ssl_mode = query_params.get('sslmode', None)
    
    connect_args = {}
    if ssl_mode == 'require':
        connect_args["ssl"] = True
    
    return create_async_engine(
        asyncpg_url,
        echo=settings.environment == "development",
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def build_asyncpg_url_and_connect_args() -> tuple[str, dict[str, object]]:
    """Return ``(asyncpg_url, connect_args)`` extracted from ``Settings``.

    Exposed separately from :func:`get_engine` so callers that need a
    custom ``poolclass`` (e.g. ``NullPool`` for the precompute path,
    which holds connections open across multi-minute idle periods) can
    reuse the same URL-building logic without going through the pooled
    engine.
    """
    settings = get_settings()
    parsed = urlparse(settings.database_url)
    netloc = parsed.netloc
    if "@" in netloc:
        userinfo, hostinfo = netloc.rsplit("@", 1)
        user, password = (userinfo.split(":", 1) + [""])[:2]
        host, port = (hostinfo.split(":", 1) + [""])[:2]
    else:
        user, password, host, port = "", "", netloc, ""
    database = parsed.path.lstrip("/") if parsed.path else ""
    url = "postgresql+asyncpg://"
    if user:
        url += user
        if password:
            url += f":{password}"
        url += "@"
    url += host
    if port:
        url += f":{port}"
    if database:
        url += f"/{database}"
    query_params = dict(
        param.split("=") for param in parsed.query.split("&") if "=" in param and param
    )
    connect_args: dict[str, object] = {}
    if query_params.get("sslmode") == "require":
        connect_args["ssl"] = True
    return url, connect_args
