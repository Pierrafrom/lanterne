"""Tests for the cross-source deduplicating upsert."""

from datetime import UTC, datetime

from sqlmodel.ext.asyncio.session import AsyncSession

from cine_event_bot.core.models import (
    EventType,
    ExtractedEvent,
    ScreeningEvent,
    Source,
)
from cine_event_bot.io.repository import EventRepository

_MOMENT = datetime(2026, 7, 1, 20, 30, tzinfo=UTC)


def _extracted(
    *,
    has_team_present: bool = False,
    description: str | None = None,
) -> ExtractedEvent:
    return ExtractedEvent(
        title="Dune",
        event_type=EventType.AVANT_PREMIERE,
        venue="Le Grand Rex",
        starts_at=_MOMENT,
        has_team_present=has_team_present,
        description=description,
    )


def _from(extracted: ExtractedEvent, source: Source, url: str) -> ScreeningEvent:
    return ScreeningEvent.from_extracted(extracted, source=source, source_url=url)


async def _count(repo: EventRepository) -> int:
    window = await repo.list_between(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)
    )
    return len(window)


async def test_upsert_inserts_a_new_event(session: AsyncSession) -> None:
    repo = EventRepository(session)

    stored = await repo.upsert(
        _from(_extracted(), Source.PREMIERE_PROJO, "https://premiereprojo.fr/dune")
    )

    assert stored.id is not None
    assert await _count(repo) == 1


async def test_upsert_same_screening_twice_keeps_one_row(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await repo.upsert(_from(_extracted(), Source.PREMIERE_PROJO, "a"))

    await repo.upsert(_from(_extracted(), Source.SORTIRAPARIS, "b"))

    assert await _count(repo) == 1


async def test_upsert_preserves_first_seen_provenance(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    first = await repo.upsert(
        _from(_extracted(), Source.PREMIERE_PROJO, "https://premiereprojo.fr/dune")
    )

    merged = await repo.upsert(
        _from(_extracted(), Source.SORTIRAPARIS, "https://sortiraparis.com/dune")
    )

    assert merged.id == first.id
    assert merged.source is Source.PREMIERE_PROJO
    assert merged.source_url == "https://premiereprojo.fr/dune"


async def test_upsert_merges_team_presence_with_logical_or(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await repo.upsert(
        _from(_extracted(has_team_present=False), Source.PREMIERE_PROJO, "a")
    )

    merged = await repo.upsert(
        _from(_extracted(has_team_present=True), Source.SORTIRAPARIS, "b")
    )

    assert merged.has_team_present is True


async def test_upsert_fills_missing_description(session: AsyncSession) -> None:
    repo = EventRepository(session)
    await repo.upsert(_from(_extracted(description=None), Source.PREMIERE_PROJO, "a"))

    merged = await repo.upsert(
        _from(
            _extracted(description="En présence du réalisateur."),
            Source.SORTIRAPARIS,
            "b",
        )
    )

    assert merged.description == "En présence du réalisateur."


async def test_upsert_keeps_existing_description(session: AsyncSession) -> None:
    repo = EventRepository(session)
    await repo.upsert(
        _from(_extracted(description="Original."), Source.PREMIERE_PROJO, "a")
    )

    merged = await repo.upsert(
        _from(_extracted(description="Replacement."), Source.SORTIRAPARIS, "b")
    )

    assert merged.description == "Original."
