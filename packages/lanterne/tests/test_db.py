"""Tests for the async database wrapper, schema management, and backups."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from factories import make_display_event
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from lanterne.core.models import ScreeningEvent
from lanterne.io.db import Database


def _event() -> ScreeningEvent:
    # Adding the event cascades to its film and venue rows.
    return make_display_event()


async def test_create_tables_allows_inserting_an_event(session: AsyncSession) -> None:
    session.add(_event())
    await session.commit()


async def test_reset_tables_wipes_all_data(database: Database) -> None:
    async with database.session() as session:
        session.add(_event())
        await session.commit()

    await database.reset_tables()

    async with database.session() as session:
        result = await session.exec(select(ScreeningEvent))
        assert result.first() is None


async def test_migrate_to_head_is_idempotent(database: Database) -> None:
    await database.migrate_to_head()
    await database.migrate_to_head()


async def test_migrate_to_head_creates_a_usable_schema(database: Database) -> None:
    await database.migrate_to_head()

    async with database.session() as session:
        result = await session.exec(select(ScreeningEvent))
        assert result.first() is None

        session.add(_event())
        await session.commit()
        version = (
            await session.exec(  # type: ignore[call-overload]
                text("SELECT version_num FROM alembic_version")
            )
        ).scalar()
    assert version is not None


async def test_backup_invokes_pg_dump_with_custom_format(tmp_path: Path) -> None:
    db = Database("postgresql+asyncpg://user:pw@dbhost:5432/lanterne")
    target = tmp_path / "snapshot.dump"
    completed = AsyncMock(returncode=0)
    completed.communicate = AsyncMock(return_value=(b"", b""))

    with patch(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=completed)
    ) as create_subprocess:
        await db.backup_to(target)

    args, _ = create_subprocess.call_args
    assert args[0] == "pg_dump"
    assert "--format=custom" in args
    assert f"--file={target}" in args
    # The libpq DSN passed to pg_dump must not carry SQLAlchemy's
    # driver-qualified scheme — pg_dump does not understand "+asyncpg".
    assert "postgresql://user:pw@dbhost:5432/lanterne" in args
    assert "+asyncpg" not in " ".join(args)


async def test_backup_raises_on_pg_dump_failure(tmp_path: Path) -> None:
    db = Database("postgresql+asyncpg://user:pw@dbhost:5432/lanterne")
    completed = MagicMock(returncode=1)
    completed.communicate = AsyncMock(return_value=(b"", b"connection refused"))

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=completed)),
        pytest.raises(RuntimeError, match="connection refused"),
    ):
        await db.backup_to(tmp_path / "snapshot.dump")
