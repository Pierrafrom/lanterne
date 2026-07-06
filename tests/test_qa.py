"""Tests for Q&A formatting, query criteria, and repository search."""

from datetime import UTC, datetime

from factories import make_display_event, make_sighting
from sqlmodel.ext.asyncio.session import AsyncSession

from cine_event_bot.core.models import EventType, ScreeningEvent
from cine_event_bot.core.qa import QueryCriteria, format_qa_answer
from cine_event_bot.io.repository import EventRepository


async def _ingest(
    repo: EventRepository,
    *,
    title: str,
    starts_at: datetime,
    event_type: EventType = EventType.AVANT_PREMIERE,
    has_team_present: bool = False,
    overview: str | None = None,
) -> ScreeningEvent:
    event = await repo.ingest(
        make_sighting(
            title=title,
            starts_at=starts_at,
            event_type=event_type,
            has_team_present=has_team_present,
        )
    )
    if overview is not None:
        event.film.overview = overview
        await repo.save_film(event.film)
    return event


def test_format_qa_answer_without_events() -> None:
    assert "aucune séance" in format_qa_answer([]).lower()


def test_format_qa_answer_lists_matches_with_date_and_venue() -> None:
    event = make_display_event(
        title="Dune", starts_at=datetime(2026, 7, 7, 18, 30, tzinfo=UTC)
    )

    answer = format_qa_answer([event])

    assert "Dune" in answer
    assert "Le Grand Rex" in answer
    assert "mardi 7 juillet à 20h30" in answer.lower()


async def test_search_filters_by_event_type(session: AsyncSession) -> None:
    repo = EventRepository(session)
    await _ingest(
        repo,
        title="Concert",
        event_type=EventType.CINE_CONCERT,
        starts_at=datetime(2026, 7, 7, 18, 0, tzinfo=UTC),
    )
    await _ingest(
        repo,
        title="Preview",
        event_type=EventType.AVANT_PREMIERE,
        starts_at=datetime(2026, 7, 8, 18, 0, tzinfo=UTC),
    )

    results = await repo.search(QueryCriteria(event_type=EventType.CINE_CONCERT))

    assert [event.film.title for event in results] == ["Concert"]


async def test_search_matches_text_in_title_or_overview(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await _ingest(
        repo,
        title="Le Voyage de Chihiro",
        starts_at=datetime(2026, 7, 7, 18, 0, tzinfo=UTC),
        overview="Un film de Hayao Miyazaki.",
    )
    await _ingest(repo, title="Dune", starts_at=datetime(2026, 7, 8, 18, 0, tzinfo=UTC))

    results = await repo.search(QueryCriteria(text_query="miyazaki"))

    assert [event.film.title for event in results] == ["Le Voyage de Chihiro"]


async def test_search_filters_team_only_and_time_window(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await _ingest(
        repo,
        title="With team",
        starts_at=datetime(2026, 7, 7, 18, 0, tzinfo=UTC),
        has_team_present=True,
    )
    await _ingest(
        repo,
        title="No team",
        starts_at=datetime(2026, 7, 7, 19, 0, tzinfo=UTC),
        has_team_present=False,
    )
    await _ingest(
        repo,
        title="Too late",
        starts_at=datetime(2026, 8, 1, 18, 0, tzinfo=UTC),
        has_team_present=True,
    )

    results = await repo.search(
        QueryCriteria(
            team_only=True,
            starts_after=datetime(2026, 7, 1, tzinfo=UTC),
            starts_before=datetime(2026, 7, 31, tzinfo=UTC),
        )
    )

    assert [event.film.title for event in results] == ["With team"]


async def test_search_without_criteria_returns_all_sorted(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await _ingest(
        repo, title="Later", starts_at=datetime(2026, 7, 9, 18, 0, tzinfo=UTC)
    )
    await _ingest(
        repo, title="Sooner", starts_at=datetime(2026, 7, 7, 18, 0, tzinfo=UTC)
    )

    results = await repo.search(QueryCriteria())

    assert [event.film.title for event in results] == ["Sooner", "Later"]
