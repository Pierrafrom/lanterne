"""Alembic migration environment (async, driven by SQLModel metadata).

The database URL comes from the same source as the application: the
``DATABASE_URL`` environment variable or ``.env`` entry, with the same default
as ``cine_event_bot.config.Settings``. Only the URL is read here — migrations
must not require the bot's tokens to be configured.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

# Import the models module for its side effect: registering every table on
# SQLModel.metadata so autogenerate sees the full schema.
from cine_event_bot.core import models as _models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


class _DatabaseSettings(BaseSettings):
    """The application's database URL, without requiring the other secrets."""

    database_url: str = "sqlite+aiosqlite:///./cine_event_bot.db"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


config.set_main_option("sqlalchemy.url", _DatabaseSettings().database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: emit SQL without a DBAPI."""
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
    """Configure the context on an open connection and run the migrations."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run the migrations through it."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    When the application invokes migrations programmatically from a running
    event loop (``Database.migrate_to_head``), it passes its own connection via
    ``config.attributes`` and this must not start a new loop; from the alembic
    CLI there is no connection yet, so an async engine is created here.
    """
    connection = config.attributes.get("connection", None)
    if connection is None:
        asyncio.run(run_async_migrations())
    else:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
