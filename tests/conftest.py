"""Shared async fixtures backed by an in-memory SQLite database."""

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlmodel.ext.asyncio.session import AsyncSession

from cine_event_bot.io.db import Database


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    yield db
    await db.dispose()


@pytest_asyncio.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    async with database.session() as active_session:
        yield active_session
