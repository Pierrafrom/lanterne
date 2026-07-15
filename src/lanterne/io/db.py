"""Async database access: engine, schema management, and session factory.

Wraps a single async SQLAlchemy engine behind a small :class:`Database` object
so the rest of the codebase depends on one explicit collaborator rather than a
module-level global engine. SQLite is driven through ``aiosqlite``.

Two schema paths coexist deliberately:

- :meth:`Database.migrate_to_head` — the production path, running Alembic
  migrations (creates and evolves the schema, records the version).
- :meth:`Database.create_tables` — the test path, building the current schema
  directly from the SQLModel metadata for fast in-memory databases.

File-backed databases run in WAL journal mode so the polling bot and the
scrape cron can read/write concurrently without ``database is locked`` errors.
"""

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

# Import the models module for its side effect: registering every table on
# SQLModel.metadata so create_tables() builds the full schema.
from lanterne.core import models as _models  # noqa: F401

_IN_MEMORY_URL_MARKER = ":memory:"


class Database:
    """Owns the async engine and hands out sessions for one SQLite database."""

    def __init__(self, url: str) -> None:
        """Build the engine and session factory for one SQLite database.

        Args:
            url: Async SQLAlchemy URL, e.g. ``sqlite+aiosqlite:///./cine.db``
                or ``sqlite+aiosqlite:///:memory:`` for tests.
        """
        self._engine = create_async_engine(url, **_engine_kwargs(url))
        if _IN_MEMORY_URL_MARKER not in url:
            _enable_wal(self._engine.sync_engine)
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

    async def migrate_to_head(self) -> None:
        """Create or upgrade the schema by running the Alembic migrations.

        Reads the migration configuration from ``alembic.ini`` and
        ``pyproject.toml`` in the current working directory (the CLI runs from
        the repository root). The engine's own connection is handed to Alembic
        so no second event loop is started.
        """
        async with self._engine.begin() as connection:
            await connection.run_sync(_upgrade_to_head)

    async def create_tables(self) -> None:
        """Create every registered table if it does not already exist.

        Test-only fast path: builds the current schema without recording an
        Alembic version. Production databases go through
        :meth:`migrate_to_head`.
        """
        async with self._engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    async def reset_tables(self) -> None:
        """Drop every table and recreate it — wipes all data."""
        async with self._engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.drop_all)
            await connection.run_sync(SQLModel.metadata.create_all)

    async def backup_to(self, target: Path) -> None:
        """Write a consistent snapshot of the database to a new file.

        Uses SQLite's ``VACUUM INTO``, which produces a compact, consistent
        copy even while the database is in use (WAL mode).

        Args:
            target: Path of the snapshot file to create; must not exist yet.
        """
        async with self._engine.connect() as connection:
            autocommit = await connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            await autocommit.exec_driver_sql("VACUUM INTO ?", (str(target),))

    def session(self) -> AsyncSession:
        """Open a new session as an async context manager.

        Returns:
            An :class:`AsyncSession` bound to this database's engine.
        """
        return self._session_factory()

    async def dispose(self) -> None:
        """Close the engine and release its connection pool."""
        await self._engine.dispose()


def _upgrade_to_head(connection: Connection) -> None:
    """Run ``alembic upgrade head`` over an already-open connection."""
    config = Config(file_="alembic.ini", toml_file="pyproject.toml")
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


def _enable_wal(sync_engine: Any) -> None:  # noqa: ANN401 — sync_engine is SQLAlchemy's untyped proxy target
    """Switch every new connection of a file-backed database to WAL mode."""

    @event.listens_for(sync_engine, "connect")
    def _set_wal(dbapi_connection: Any, _record: Any) -> None:  # noqa: ANN401 — DBAPI connection has no common type
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def _engine_kwargs(url: str) -> dict[str, object]:
    """Return engine kwargs tuned for the SQLite connection style.

    An in-memory database lives only inside a single connection, so a
    :class:`StaticPool` is required for the schema and data to stay visible
    across the multiple sessions opened against the same engine.

    Args:
        url: The async SQLAlchemy URL the engine is built from.

    Returns:
        Keyword arguments to pass to ``create_async_engine``.
    """
    if _IN_MEMORY_URL_MARKER in url:
        return {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    return {}
