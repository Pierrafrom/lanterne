"""Shared commit helper for every repository in this package."""

from sqlmodel.ext.asyncio.session import AsyncSession


async def commit(session: AsyncSession) -> None:
    """Commit the session, rolling back on failure to keep it reusable.

    An uncaught commit failure (e.g. a unique-constraint collision) leaves an
    AsyncSession in SQLAlchemy's "pending rollback" state, where every
    subsequent operation raises ``PendingRollbackError`` regardless of what it
    does — turning one bad row into a crash of the whole ingestion run instead
    of the one enrichment or sighting that actually failed.
    """
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
