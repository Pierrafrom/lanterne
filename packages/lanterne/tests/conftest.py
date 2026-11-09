"""Shared async fixtures backed by a real PostgreSQL container.

See ``docs/decisions/0016-tests-on-postgres.md``: the suite runs Alembic's
real migration path (:meth:`Database.migrate_to_head`) against a genuine
PostgreSQL instance via ``testcontainers``, not a SQLite fast path — so the
tables, JSONB columns, and constraints exercised here are the ones
production actually creates. One container is shared for the whole test
session; each test gets isolation by truncating every table beforehand
rather than by starting a fresh container.
"""

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession
from testcontainers.postgres import PostgresContainer

from lanterne.io.db import Database

_TABLES = (
    "eventsighting",
    "screeningevent",
    "filmrating",
    "film",
    "screen",
    "venue",
    "subscriber",
)


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Start one PostgreSQL container for the whole test session.

    Uses the same ``postgis/postgis:18-3.6`` image as ``docker-compose.yml``
    (PostGIS-enabled), and mirrors its ``shared_preload_libraries``
    setting — ``pg_stat_statements`` must be loaded at server start, so
    ``CREATE EXTENSION pg_stat_statements`` (see the search-extensions
    migration) fails here too if this container doesn't preload it the same
    way production does.
    """
    container = PostgresContainer("postgis/postgis:18-3.6", driver="asyncpg")
    container.with_command("postgres -c shared_preload_libraries=pg_stat_statements")
    with container:
        yield container.get_connection_url()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _migrated_schema(postgres_url: str) -> None:
    """Run the real Alembic migration path once, before any test runs."""
    db = Database(postgres_url)
    await db.migrate_to_head()
    await db.dispose()


@pytest_asyncio.fixture
async def database(postgres_url: str) -> AsyncIterator[Database]:
    """Hand out a :class:`Database` bound to the shared container.

    Truncates every table first — cheap, schema-stable isolation between
    tests, instead of recreating the schema (or a whole new container) for
    each one.
    """
    db = Database(postgres_url)
    async with db.session() as session:
        await session.exec(  # type: ignore[call-overload]
            text(f"TRUNCATE TABLE {', '.join(_TABLES)} RESTART IDENTITY CASCADE")
        )
        await session.commit()
    yield db
    await db.dispose()


@pytest_asyncio.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    async with database.session() as active_session:
        yield active_session
