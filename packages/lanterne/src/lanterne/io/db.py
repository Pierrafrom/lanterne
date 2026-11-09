"""Async database access: engine, schema management, and session factory.

Wraps a single async SQLAlchemy engine behind a small :class:`Database` object
so the rest of the codebase depends on one explicit collaborator rather than a
module-level global engine. PostgreSQL is driven through ``asyncpg`` (see
``docs/decisions/0014-postgres-migration.md``).

Schema changes are managed exclusively through Alembic
(:meth:`Database.migrate_to_head`) — there is no ad hoc "build the schema
straight from metadata" fast path for tests. The test suite runs this same
production path against a real PostgreSQL container (see
``docs/decisions/0016-tests-on-postgres.md``), so a migration that passes in
tests is a genuine signal it works in production.
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

# Import the models module for its side effect: registering every table on
# SQLModel.metadata so reset_tables() can drop/recreate the full schema.
from lanterne.core import models as _models  # noqa: F401

# Anchored to this file's own location, not the caller's working directory —
# alembic.ini/pyproject.toml live at the package root (packages/lanterne/),
# two levels above src/lanterne/, regardless of which directory a script or
# container entrypoint happens to run from.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class Database:
    """Owns the async engine and hands out sessions for one PostgreSQL database."""

    def __init__(self, url: str) -> None:
        """Build the engine and session factory for one PostgreSQL database.

        Args:
            url: Async SQLAlchemy URL, e.g.
                ``postgresql+asyncpg://lanterne:lanterne@localhost:5432/lanterne``.
        """
        self._url = url
        self._engine = create_async_engine(url)
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

    async def migrate_to_head(self) -> None:
        """Create or upgrade the schema by running the Alembic migrations.

        Reads the migration configuration from ``alembic.ini`` and
        ``pyproject.toml`` at the package root (``packages/lanterne/``),
        resolved from this file's own location rather than the caller's
        working directory. The engine's own connection is handed to Alembic
        so no second event loop is started.
        """
        async with self._engine.begin() as connection:
            await connection.run_sync(_upgrade_to_head)

    async def reset_tables(self) -> None:
        """Drop every table and recreate it — wipes all data."""
        async with self._engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.drop_all)
            await connection.run_sync(SQLModel.metadata.create_all)

    async def backup_to(self, target: Path) -> None:
        """Write a consistent snapshot of the database via ``pg_dump``.

        Uses ``pg_dump``'s custom format (``-Fc``): compressed, and
        restorable selectively or into a differently-named database with
        ``pg_restore``, unlike a plain SQL dump.

        Args:
            target: Path of the snapshot file to create; must not exist yet.

        Raises:
            RuntimeError: If ``pg_dump`` exits with a non-zero status.
        """
        # pg_dump speaks libpq URIs (postgresql://), not SQLAlchemy's
        # driver-qualified postgresql+asyncpg:// scheme.
        dsn = self._url.replace("postgresql+asyncpg://", "postgresql://", 1)
        process = await asyncio.create_subprocess_exec(
            "pg_dump",
            "--format=custom",
            f"--file={target}",
            dsn,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {stderr.decode().strip()}")

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
    config = Config(
        file_=str(_PACKAGE_ROOT / "alembic.ini"),
        toml_file=str(_PACKAGE_ROOT / "pyproject.toml"),
    )
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
