import asyncio
from logging.config import fileConfig
from urllib.parse import urlparse

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from core.config import get_settings
from infrastructure.db.climate_asset_model import Base

config = context.config
settings = get_settings()

# Parse the database URL and build asyncpg-compatible URL
parsed = urlparse(settings.database_url)
# Build URL without query parameters for asyncpg
asyncpg_url = f"postgresql+asyncpg://{parsed.netloc}{parsed.path}"
config.set_main_option("sqlalchemy.url", asyncpg_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # Parse URL and check for SSL requirement
    parsed = urlparse(settings.database_url)
    query_params = dict(param.split('=') for param in parsed.query.split('&') if '=' in param and param)
    ssl_mode = query_params.get('sslmode', None)
    
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"ssl": True} if ssl_mode == 'require' else {},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
