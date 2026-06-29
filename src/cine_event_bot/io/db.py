"""Async database access: engine, schema creation, and session factory.

Wraps a single async SQLAlchemy engine behind a small :class:`Database` object
so the rest of the codebase depends on one explicit collaborator rather than a
module-level global engine. SQLite is driven through ``aiosqlite``.
"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

# Import the models module for its side effect: registering every table on
# SQLModel.metadata so create_tables() builds the full schema.
from cine_event_bot.core import models as _models  # noqa: F401

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
        self._session_factory = async_sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

    async def create_tables(self) -> None:
        """Create every registered table if it does not already exist."""
        async with self._engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    async def reset_tables(self) -> None:
        """Drop every table and recreate it — wipes all data."""
        async with self._engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.drop_all)
            await connection.run_sync(SQLModel.metadata.create_all)

    def session(self) -> AsyncSession:
        """Open a new session as an async context manager.

        Returns:
            An :class:`AsyncSession` bound to this database's engine.
        """
        return self._session_factory()

    async def dispose(self) -> None:
        """Close the engine and release its connection pool."""
        await self._engine.dispose()


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
