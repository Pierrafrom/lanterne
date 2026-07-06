"""Tests for the event and subscriber repositories."""

from datetime import UTC, datetime

from factories import make_sighting
from sqlmodel.ext.asyncio.session import AsyncSession

from cine_event_bot.core.models import ScreeningEvent
from cine_event_bot.io.repository import EventRepository, SubscriberRepository


async def _ingest(
    repo: EventRepository, *, title: str = "Dune", starts_at: datetime
) -> ScreeningEvent:
    return await repo.ingest(make_sighting(title=title, starts_at=starts_at))


async def test_ingest_then_get_by_dedup_key_roundtrip(session: AsyncSession) -> None:
    repo = EventRepository(session)
    moment = datetime(2026, 7, 1, 20, 30, tzinfo=UTC)

    stored = await _ingest(repo, starts_at=moment)
    found = await repo.get_by_dedup_key(stored.dedup_key)

    assert found is not None
    assert found.film.title == "Dune"


async def test_get_by_dedup_key_returns_none_when_absent(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)

    assert await repo.get_by_dedup_key("missing") is None


async def test_starts_at_round_trips_as_utc_aware(session: AsyncSession) -> None:
    repo = EventRepository(session)
    moment = datetime(2026, 7, 7, 18, 30, tzinfo=UTC)
    stored = await _ingest(repo, starts_at=moment)

    found = await repo.get_by_dedup_key(stored.dedup_key)

    assert found is not None
    assert found.starts_at.tzinfo is not None  # not naive (SQLite default)
    assert found.starts_at == moment


async def test_list_between_filters_on_start_time(session: AsyncSession) -> None:
    repo = EventRepository(session)
    await _ingest(
        repo, title="Inside", starts_at=datetime(2026, 7, 2, 20, 0, tzinfo=UTC)
    )
    await _ingest(
        repo, title="Before", starts_at=datetime(2026, 6, 1, 20, 0, tzinfo=UTC)
    )
    await _ingest(
        repo, title="After", starts_at=datetime(2026, 8, 1, 20, 0, tzinfo=UTC)
    )

    window = await repo.list_between(
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 8, tzinfo=UTC),
    )

    assert [event.film.title for event in window] == ["Inside"]


async def test_list_between_orders_by_start_time(session: AsyncSession) -> None:
    repo = EventRepository(session)
    await _ingest(
        repo, title="Later", starts_at=datetime(2026, 7, 5, 20, 0, tzinfo=UTC)
    )
    await _ingest(
        repo, title="Sooner", starts_at=datetime(2026, 7, 2, 20, 0, tzinfo=UTC)
    )

    window = await repo.list_between(
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 8, tzinfo=UTC),
    )

    assert [event.film.title for event in window] == ["Sooner", "Later"]


async def test_stats_on_empty_database(session: AsyncSession) -> None:
    stats = await EventRepository(session).stats()

    assert stats.total == 0
    assert stats.first_starts_at is None


async def test_stats_summarizes_stored_events(session: AsyncSession) -> None:
    repo = EventRepository(session)
    first = await _ingest(repo, starts_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC))
    await _ingest(repo, starts_at=datetime(2026, 7, 5, 20, 0, tzinfo=UTC))
    first.film.tmdb_id = 693134
    await repo.save_film(first.film)

    stats = await repo.stats()

    assert stats.total == 2
    # by_source counts sightings, one per (event, source) pair.
    assert stats.by_source == {"premiereprojo.fr": 2}
    assert stats.by_type == {"avant_premiere": 2}
    # Both screenings share the film row, so its TMDB match counts for both.
    assert stats.enriched == 2
    # SQLite returns datetimes timezone-naive; only ordering and date matter here.
    assert stats.first_starts_at is not None
    assert stats.last_starts_at is not None
    assert stats.first_starts_at < stats.last_starts_at
    assert stats.first_starts_at.day == 1


async def test_subscribe_creates_active_subscriber(session: AsyncSession) -> None:
    repo = SubscriberRepository(session)

    await repo.subscribe(chat_id=42)
    active = await repo.list_active()

    assert [sub.chat_id for sub in active] == [42]


async def test_subscribe_is_idempotent_and_reactivates(
    session: AsyncSession,
) -> None:
    repo = SubscriberRepository(session)
    await repo.subscribe(chat_id=42)
    await repo.unsubscribe(chat_id=42)

    await repo.subscribe(chat_id=42)
    active = await repo.list_active()

    assert [sub.chat_id for sub in active] == [42]


async def test_unsubscribe_excludes_from_active_list(session: AsyncSession) -> None:
    repo = SubscriberRepository(session)
    await repo.subscribe(chat_id=42)

    await repo.unsubscribe(chat_id=42)

    assert await repo.list_active() == []


async def test_unsubscribe_unknown_chat_is_noop(session: AsyncSession) -> None:
    repo = SubscriberRepository(session)

    await repo.unsubscribe(chat_id=999)

    assert await repo.list_active() == []
