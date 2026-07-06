"""Tests for the async database wrapper and schema creation."""

from factories import make_display_event
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from cine_event_bot.core.models import ScreeningEvent
from cine_event_bot.io.db import Database


def _event() -> ScreeningEvent:
    # Adding the event cascades to its film and venue rows.
    return make_display_event()


async def test_create_tables_allows_inserting_an_event(session: AsyncSession) -> None:
    session.add(_event())
    await session.commit()


async def test_in_memory_database_starts_empty() -> None:
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    async with db.session() as session:
        result = await session.exec(select(ScreeningEvent))
        assert result.first() is None
    await db.dispose()


async def test_reset_tables_wipes_all_data(database: Database) -> None:
    async with database.session() as session:
        session.add(_event())
        await session.commit()

    await database.reset_tables()

    async with database.session() as session:
        result = await session.exec(select(ScreeningEvent))
        assert result.first() is None
