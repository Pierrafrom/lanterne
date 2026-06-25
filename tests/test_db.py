"""Tests for the async database wrapper and schema creation."""

from datetime import UTC, datetime

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from cine_event_bot.core.models import EventType, ScreeningEvent, Source
from cine_event_bot.io.db import Database


def _event() -> ScreeningEvent:
    return ScreeningEvent(
        dedup_key="key-1",
        title="Dune",
        event_type=EventType.AVANT_PREMIERE,
        venue="Le Grand Rex",
        starts_at=datetime(2026, 7, 1, 20, 30, tzinfo=UTC),
        source=Source.PREMIERE_PROJO,
    )


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
