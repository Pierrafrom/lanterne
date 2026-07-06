"""Tests for the async database wrapper, schema management, and backups."""

from pathlib import Path

from factories import make_display_event
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from cine_event_bot.core.models import ScreeningEvent
from cine_event_bot.io.db import Database


def _event() -> ScreeningEvent:
    # Adding the event cascades to its film and venue rows.
    return make_display_event()


def _file_db(tmp_path: Path, name: str = "test.db") -> Database:
    return Database(f"sqlite+aiosqlite:///{tmp_path / name}")


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


async def test_file_database_runs_in_wal_mode(tmp_path: Path) -> None:
    db = _file_db(tmp_path)
    await db.create_tables()

    async with db.session() as session:
        mode = (await session.exec(text("PRAGMA journal_mode"))).scalar()  # type: ignore[call-overload]

    assert mode == "wal"
    await db.dispose()


async def test_migrate_to_head_creates_a_usable_schema(tmp_path: Path) -> None:
    db = _file_db(tmp_path)

    await db.migrate_to_head()

    async with db.session() as session:
        session.add(_event())
        await session.commit()
        version = (
            await session.exec(text("SELECT version_num FROM alembic_version"))  # type: ignore[call-overload]
        ).scalar()
    assert version is not None
    await db.dispose()


async def test_migrate_to_head_is_idempotent(tmp_path: Path) -> None:
    db = _file_db(tmp_path)

    await db.migrate_to_head()
    await db.migrate_to_head()

    await db.dispose()


async def test_backup_produces_a_consistent_snapshot(tmp_path: Path) -> None:
    db = _file_db(tmp_path)
    await db.create_tables()
    async with db.session() as session:
        session.add(_event())
        await session.commit()

    target = tmp_path / "snapshot.db"
    await db.backup_to(target)
    await db.dispose()

    restored = Database(f"sqlite+aiosqlite:///{target}")
    async with restored.session() as session:
        result = await session.exec(select(ScreeningEvent))
        assert result.first() is not None
    await restored.dispose()
