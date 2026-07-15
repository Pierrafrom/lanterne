"""Tests for the event and subscriber repositories."""

from datetime import UTC, datetime, timedelta

import pytest
from factories import make_sighting
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from lanterne.core.models import (
    EventType,
    Film,
    RatingSource,
    ScreeningEvent,
    Venue,
    VenueKind,
)
from lanterne.io.repository import EventRepository, SubscriberRepository
from lanterne.io.scrapers.base import RatingRecord, VenueDetail


async def _ingest(
    repo: EventRepository,
    *,
    title: str = "Dune",
    starts_at: datetime,
    venue: str = "Le Grand Rex",
    event_type: EventType | None = EventType.AVANT_PREMIERE,
) -> ScreeningEvent:
    return await repo.ingest(
        make_sighting(
            title=title, starts_at=starts_at, venue=venue, event_type=event_type
        )
    )


async def test_ingest_then_get_by_dedup_key_roundtrip(session: AsyncSession) -> None:
    repo = EventRepository(session)
    moment = datetime(2026, 7, 1, 20, 30, tzinfo=UTC)

    stored = await _ingest(repo, starts_at=moment)
    found = await repo.get_by_dedup_key(stored.dedup_key)

    assert found is not None
    assert found.film.title == "Dune"


async def test_ingest_collapses_a_known_venue_alias_onto_one_venue_row(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)

    short_form = await repo.ingest(
        make_sighting(title="A", venue="Forum des images", starts_at=_moment())
    )
    long_form = await repo.ingest(
        make_sighting(title="B", venue="Le Forum des images", starts_at=_moment())
    )

    assert short_form.venue_id == long_form.venue_id
    assert short_form.venue.name == "Le Forum des images"


def _moment() -> datetime:
    return datetime(2026, 7, 1, 20, 30, tzinfo=UTC)


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
    assert stats.by_venue_kind == {"independent": 2}
    assert stats.special_count == 2
    assert stats.specialization_rate == 1.0


async def test_stats_specialization_rate_on_an_empty_database(
    session: AsyncSession,
) -> None:
    stats = await EventRepository(session).stats()

    assert stats.specialization_rate == 0.0


async def test_stats_specialization_rate_with_a_mix_of_special_and_ordinary(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await _ingest(repo, starts_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC))
    await _ingest(
        repo,
        title="Ordinary",
        starts_at=datetime(2026, 7, 2, 20, 0, tzinfo=UTC),
        event_type=None,
    )

    stats = await repo.stats()

    assert stats.special_count == 1
    assert stats.specialization_rate == 0.5


async def test_save_event_persists_in_place_changes(session: AsyncSession) -> None:
    repo = EventRepository(session)
    event = await _ingest(repo, starts_at=_moment(), event_type=None)
    event.is_special = True
    event.specialness_reasons = ["repertory_rarity"]

    await repo.save_event(event)
    reloaded = await repo.get_by_dedup_key(event.dedup_key)

    assert reloaded is not None
    assert reloaded.is_special is True
    assert reloaded.specialness_reasons == ["repertory_rarity"]


async def test_save_film_rolls_back_on_commit_failure_so_session_stays_usable(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    first = await _ingest(
        repo, title="First", starts_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC)
    )
    second = await _ingest(
        repo, title="Second", starts_at=datetime(2026, 7, 2, 20, 0, tzinfo=UTC)
    )
    first.film.tmdb_id = 693134
    await repo.save_film(first.film)
    second.film.tmdb_id = 693134  # same TMDB id: violates the unique constraint

    with pytest.raises(IntegrityError):
        await repo.save_film(second.film)

    # A poisoned session would raise PendingRollbackError here instead of
    # running the query — this is what crashed a live scrape run.
    stats = await repo.stats()
    assert stats.total == 2


async def test_find_film_by_tmdb_id_returns_the_matching_film(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    event = await _ingest(repo, starts_at=_moment())
    event.film.tmdb_id = 693134
    await repo.save_film(event.film)

    found = await repo.find_film_by_tmdb_id(693134)

    assert found is not None
    assert found.id == event.film_id


async def test_find_film_by_tmdb_id_returns_none_when_absent(
    session: AsyncSession,
) -> None:
    assert await EventRepository(session).find_film_by_tmdb_id(999) is None


async def test_merge_film_repoints_the_losers_screenings_onto_the_winner(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    winner_event = await _ingest(
        repo, title="Title A", starts_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC)
    )
    loser_event = await _ingest(
        repo, title="Title B", starts_at=datetime(2026, 7, 2, 20, 0, tzinfo=UTC)
    )
    winner_event.film.tmdb_id = 693134
    await repo.save_film(winner_event.film)
    loser_film = loser_event.film

    await repo.merge_film(loser=loser_film, winner=winner_event.film)

    reloaded_loser_event = await repo.get_by_dedup_key(loser_event.dedup_key)
    assert reloaded_loser_event is not None
    assert reloaded_loser_event.film_id == winner_event.film_id
    assert await repo.find_film_by_tmdb_id(693134) is not None
    stats = await repo.stats()
    # Both screenings still exist; the duplicate film row is gone.
    assert stats.total == 2
    film_count = await session.exec(select(func.count()).select_from(Film))
    assert film_count.one() == 1


async def test_find_venue_by_external_id_returns_the_matching_venue(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    event = await _ingest(repo, starts_at=_moment(), venue="Le Louxor")
    event.venue.paris_cine_info_tid = "W7510"
    session.add(event.venue)
    await session.commit()

    found = await repo.find_venue_by_external_id("W7510")

    assert found is not None
    assert found.id == event.venue_id


async def test_find_venue_by_external_id_returns_none_when_absent(
    session: AsyncSession,
) -> None:
    assert await EventRepository(session).find_venue_by_external_id("nope") is None


async def test_merge_venue_repoints_the_losers_screenings_onto_the_winner(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    winner_event = await _ingest(
        repo,
        title="Title A",
        starts_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC),
        venue="MK2 Nation",
    )
    loser_event = await _ingest(
        repo,
        title="Title B",
        starts_at=datetime(2026, 7, 2, 20, 0, tzinfo=UTC),
        venue="MK2 Nation (Cours de Vincennes)",
    )
    assert winner_event.venue_id != loser_event.venue_id

    await repo.merge_venue(loser=loser_event.venue, winner=winner_event.venue)

    reloaded_loser_event = await repo.get_by_dedup_key(loser_event.dedup_key)
    assert reloaded_loser_event is not None
    assert reloaded_loser_event.venue_id == winner_event.venue_id
    venue_count = await session.exec(select(func.count()).select_from(Venue))
    assert venue_count.one() == 1


async def test_ingest_stamps_a_paris_cine_info_tid_onto_an_existing_venue(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    first = await repo.ingest(
        make_sighting(
            title="A", venue="MK2 Bastille (Beaumarchais)", starts_at=_moment()
        )
    )

    second = await repo.ingest(
        make_sighting(
            title="B",
            venue="MK2 Bastille (Beaumarchais)",
            starts_at=_moment() + timedelta(days=1),
            venue_external_id="C0140",
        )
    )

    assert first.venue_id == second.venue_id
    assert second.venue.paris_cine_info_tid == "C0140"


async def test_ingest_merges_a_fragmented_venue_row_once_its_tid_is_seen(
    session: AsyncSession,
) -> None:
    """Two wordings of one physical venue self-heal once a common tid is seen.

    Reproduces the real venue-name fragmentation confirmed live (16 `Venue`
    rows for MK2's 11 physical rooms, see
    ``docs/decisions/0012-retire-lechampo.md``): a bespoke-scraper wording
    and Paris Ciné Info's own wording create two rows until a showtime
    carrying the shared tid is ingested for the second wording, which merges
    it into the tid-owning row instead of leaving two.
    """
    repo = EventRepository(session)
    tid_owner = await repo.ingest(
        make_sighting(
            title="A",
            venue="MK2 Bastille Beaumarchais",
            starts_at=_moment(),
            venue_external_id="C0140",
        )
    )
    fragmented = await repo.ingest(
        make_sighting(
            title="B",
            venue="MK2 Bastille (Beaumarchais)",
            starts_at=_moment() + timedelta(days=1),
        )
    )
    # Confirm the fragmentation is real before healing it.
    assert fragmented.venue_id != tid_owner.venue_id

    healed = await repo.ingest(
        make_sighting(
            title="C",
            venue="MK2 Bastille (Beaumarchais)",
            starts_at=_moment() + timedelta(days=2),
            venue_external_id="C0140",
        )
    )

    assert healed.venue_id == tid_owner.venue_id
    reloaded_fragmented = await repo.get_by_dedup_key(fragmented.dedup_key)
    assert reloaded_fragmented is not None
    assert reloaded_fragmented.venue_id == tid_owner.venue_id
    venue_count = await session.exec(select(func.count()).select_from(Venue))
    assert venue_count.one() == 1


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


async def test_ingest_flags_special_when_event_type_is_set(
    session: AsyncSession,
) -> None:
    event = await _ingest(EventRepository(session), starts_at=_moment())

    assert event.is_special is True
    assert event.specialness_reasons == ["curated_source"]


async def test_ingest_leaves_ordinary_screening_unflagged(
    session: AsyncSession,
) -> None:
    event = await _ingest(
        EventRepository(session), starts_at=_moment(), event_type=None
    )

    assert event.event_type is None
    assert event.is_special is False
    assert event.specialness_reasons is None


async def test_merge_upgrades_is_special_when_a_later_sighting_carries_a_type(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    moment = _moment()
    await repo.ingest(
        make_sighting(
            title="Dune", venue="Le Grand Rex", starts_at=moment, event_type=None
        )
    )

    merged = await repo.ingest(
        make_sighting(
            title="Dune",
            venue="Le Grand Rex",
            starts_at=moment,
            event_type=EventType.SEANCE_CULTE,
        )
    )

    assert merged.is_special is True
    assert merged.specialness_reasons == ["curated_source"]


async def test_ingest_falls_back_to_source_url_when_no_booking_url(
    session: AsyncSession,
) -> None:
    event = await EventRepository(session).ingest(
        make_sighting(
            starts_at=_moment(),
            source_url="https://example.com/announcement",
            booking_url=None,
        )
    )

    assert event.booking_url == "https://example.com/announcement"


async def test_ingest_prefers_booking_url_over_source_url(
    session: AsyncSession,
) -> None:
    event = await EventRepository(session).ingest(
        make_sighting(
            starts_at=_moment(),
            source_url="https://example.com/announcement",
            booking_url="https://example.com/tickets",
        )
    )

    assert event.booking_url == "https://example.com/tickets"


async def test_merge_backfills_booking_url_falling_back_to_source_url(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    moment = _moment()
    await repo.ingest(
        make_sighting(
            title="Dune",
            venue="Le Grand Rex",
            starts_at=moment,
            source_url=None,
            booking_url=None,
        )
    )

    merged = await repo.ingest(
        make_sighting(
            title="Dune",
            venue="Le Grand Rex",
            starts_at=moment,
            source_url="https://example.com/announcement",
            booking_url=None,
        )
    )

    assert merged.booking_url == "https://example.com/announcement"


async def test_list_between_only_special_excludes_ordinary_screenings(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await _ingest(
        repo, title="Special", starts_at=datetime(2026, 7, 2, 20, 0, tzinfo=UTC)
    )
    await _ingest(
        repo,
        title="Ordinary",
        starts_at=datetime(2026, 7, 3, 20, 0, tzinfo=UTC),
        event_type=None,
    )

    window = await repo.list_between(
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 8, tzinfo=UTC),
        only_special=True,
    )

    assert [event.film.title for event in window] == ["Special"]


async def test_list_between_without_only_special_includes_everything(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await _ingest(
        repo, title="Special", starts_at=datetime(2026, 7, 2, 20, 0, tzinfo=UTC)
    )
    await _ingest(
        repo,
        title="Ordinary",
        starts_at=datetime(2026, 7, 3, 20, 0, tzinfo=UTC),
        event_type=None,
    )

    window = await repo.list_between(
        datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 8, tzinfo=UTC)
    )

    assert {event.film.title for event in window} == {"Special", "Ordinary"}


async def test_stats_by_type_labels_an_ordinary_screening_as_regular(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await _ingest(repo, starts_at=_moment(), event_type=None)

    stats = await repo.stats()

    assert stats.by_type == {"regular": 1}


async def test_resolve_venue_classifies_a_new_venue_by_name(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)

    event = await _ingest(repo, starts_at=_moment(), venue="UGC Opéra")

    assert event.venue.kind is VenueKind.CHAIN_UGC


async def test_update_venue_passes_sets_accepted_passes_on_an_existing_venue(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    event = await _ingest(repo, starts_at=_moment(), venue="Le Grand Rex")

    await repo.update_venue_passes({"Le Grand Rex": ["ugc", "pass"]})

    updated = await repo.get_by_dedup_key(event.dedup_key)
    assert updated is not None
    assert updated.venue.accepted_passes == ["pass", "ugc"]


async def test_update_venue_passes_skips_a_venue_not_yet_stored(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)

    # Should not raise, and should not create a Venue row from this alone.
    await repo.update_venue_passes({"Cinéma Inconnu": ["ugc"]})

    stats = await repo.stats()
    assert stats.total == 0


async def test_update_venue_passes_matches_a_known_venue_alias(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    event = await _ingest(repo, starts_at=_moment(), venue="Forum des images")

    await repo.update_venue_passes({"Le Forum des images": ["ugc"]})

    updated = await repo.get_by_dedup_key(event.dedup_key)
    assert updated is not None
    assert updated.venue.accepted_passes == ["ugc"]


async def test_update_venue_details_sets_address_website_and_room_fields(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    event = await _ingest(repo, starts_at=_moment(), venue="Le Louxor")

    await repo.update_venue_details(
        {
            "Le Louxor": VenueDetail(
                address="170 Boulevard de Magenta 75010 Paris 10e",
                website="https://www.cinemalouxor.fr/films/",
                seat_count=334,
                screen_width_m=9.0,
                screen_height_m=5.0,
            )
        }
    )

    updated = await repo.get_by_dedup_key(event.dedup_key)
    assert updated is not None
    assert updated.venue.address == "170 Boulevard de Magenta 75010 Paris 10e"
    assert updated.venue.website == "https://www.cinemalouxor.fr/films/"
    assert updated.venue.seat_count == 334
    assert updated.venue.screen_width_m == 9.0
    assert updated.venue.screen_height_m == 5.0


async def test_update_venue_details_skips_a_venue_not_yet_stored(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)

    await repo.update_venue_details({"Cinéma Inconnu": VenueDetail(seat_count=100)})

    stats = await repo.stats()
    assert stats.total == 0


async def test_update_venue_details_never_overwrites_a_known_field_with_none(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    event = await _ingest(repo, starts_at=_moment(), venue="Le Louxor")
    await repo.update_venue_details({"Le Louxor": VenueDetail(seat_count=334)})

    # A later, multi-room run that could not determine seat_count must not
    # blank out the value already known from an earlier, single-room run.
    await repo.update_venue_details(
        {"Le Louxor": VenueDetail(address="170 Boulevard de Magenta")}
    )

    updated = await repo.get_by_dedup_key(event.dedup_key)
    assert updated is not None
    assert updated.venue.seat_count == 334
    assert updated.venue.address == "170 Boulevard de Magenta"


async def _ingest_with_imdb_id(
    repo: EventRepository, *, imdb_id: str = "tt0055852"
) -> ScreeningEvent:
    event = await _ingest(repo, starts_at=_moment())
    event.film.imdb_id = imdb_id
    await repo.save_film(event.film)
    return event


async def test_update_film_ratings_inserts_new_ratings(session: AsyncSession) -> None:
    repo = EventRepository(session)
    event = await _ingest_with_imdb_id(repo)

    await repo.update_film_ratings(
        {
            "tt0055852": [
                RatingRecord(source=RatingSource.IMDB, rating=7.8, url="https://imdb"),
                RatingRecord(source=RatingSource.LETTERBOXD, rating=4.2, url=None),
            ]
        }
    )

    ratings = await repo.list_film_ratings(event.film_id)
    by_source = {rating.source: rating for rating in ratings}
    assert by_source[RatingSource.IMDB].rating == 7.8
    assert by_source[RatingSource.IMDB].url == "https://imdb"
    assert by_source[RatingSource.LETTERBOXD].rating == 4.2


async def test_update_film_ratings_refreshes_an_existing_source(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    event = await _ingest_with_imdb_id(repo)
    await repo.update_film_ratings(
        {"tt0055852": [RatingRecord(source=RatingSource.IMDB, rating=7.8)]}
    )

    await repo.update_film_ratings(
        {"tt0055852": [RatingRecord(source=RatingSource.IMDB, rating=8.0)]}
    )

    ratings = await repo.list_film_ratings(event.film_id)
    assert len(ratings) == 1
    assert ratings[0].rating == 8.0


async def test_update_film_ratings_skips_a_film_not_matched_by_imdb_id(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await _ingest(repo, starts_at=_moment())  # no imdb_id set

    # Should not raise, and should create no FilmRating row.
    await repo.update_film_ratings(
        {"tt9999999": [RatingRecord(source=RatingSource.IMDB, rating=5.0)]}
    )


async def test_prune_deletes_an_old_ordinary_screening(session: AsyncSession) -> None:
    repo = EventRepository(session)
    old = await _ingest(
        repo,
        title="Old",
        starts_at=datetime(2026, 1, 1, 20, 0, tzinfo=UTC),
        event_type=None,
    )

    deleted = await repo.prune_ordinary_screenings(datetime(2026, 6, 1, tzinfo=UTC))

    assert deleted == 1
    assert await repo.get_by_dedup_key(old.dedup_key) is None


async def test_prune_deletes_the_events_sightings_too(session: AsyncSession) -> None:
    repo = EventRepository(session)
    old = await _ingest(
        repo,
        title="Old",
        starts_at=datetime(2026, 1, 1, 20, 0, tzinfo=UTC),
        event_type=None,
    )

    await repo.prune_ordinary_screenings(datetime(2026, 6, 1, tzinfo=UTC))

    assert old.id is not None
    assert await repo.list_sightings(old.id) == []


async def test_prune_never_deletes_a_special_screening_regardless_of_age(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    old_special = await _ingest(
        repo, title="Old Special", starts_at=datetime(2026, 1, 1, 20, 0, tzinfo=UTC)
    )

    deleted = await repo.prune_ordinary_screenings(datetime(2026, 6, 1, tzinfo=UTC))

    assert deleted == 0
    assert await repo.get_by_dedup_key(old_special.dedup_key) is not None


async def test_prune_keeps_a_recent_ordinary_screening(session: AsyncSession) -> None:
    repo = EventRepository(session)
    recent = await _ingest(
        repo,
        title="Recent",
        starts_at=datetime(2026, 7, 5, 20, 0, tzinfo=UTC),
        event_type=None,
    )

    deleted = await repo.prune_ordinary_screenings(datetime(2026, 6, 1, tzinfo=UTC))

    assert deleted == 0
    assert await repo.get_by_dedup_key(recent.dedup_key) is not None


async def test_prune_on_an_empty_database_returns_zero(session: AsyncSession) -> None:
    repo = EventRepository(session)

    assert await repo.prune_ordinary_screenings(datetime(2026, 6, 1, tzinfo=UTC)) == 0


async def test_venue_counts_by_film_counts_across_all_screenings_of_a_film(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    first = await _ingest(
        repo, title="Dune", venue="Le Grand Rex", starts_at=_moment(), event_type=None
    )
    await _ingest(
        repo,
        title="Dune",
        venue="UGC Opéra",
        starts_at=_moment(),
        event_type=None,
    )
    # A different film must not count toward Dune's venue count.
    await _ingest(
        repo, title="Other", venue="Le Champo", starts_at=_moment(), event_type=None
    )

    counts = await repo.venue_counts_by_film()

    assert counts[first.film_id] == 2


async def test_venue_counts_by_film_omits_a_film_with_no_screenings(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    event = await _ingest(repo, starts_at=_moment(), event_type=None)
    other_film_id = event.film_id + 1

    counts = await repo.venue_counts_by_film()

    assert other_film_id not in counts


async def test_screening_counts_by_film_and_venue_scopes_to_one_pair(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    first = await _ingest(
        repo,
        title="Dune",
        venue="Le Grand Rex",
        starts_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC),
        event_type=None,
    )
    await _ingest(
        repo,
        title="Dune",
        venue="Le Grand Rex",
        starts_at=datetime(2026, 7, 2, 20, 0, tzinfo=UTC),
        event_type=None,
    )
    # Same film, different venue: must not count toward the Grand Rex total.
    await _ingest(
        repo,
        title="Dune",
        venue="UGC Opéra",
        starts_at=datetime(2026, 7, 3, 20, 0, tzinfo=UTC),
        event_type=None,
    )

    counts = await repo.screening_counts_by_film_and_venue()

    assert counts[(first.film_id, first.venue_id)] == 2


async def test_list_ordinary_screenings_excludes_special_ones(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await _ingest(repo, title="Special", starts_at=_moment())
    await _ingest(repo, title="Ordinary", starts_at=_moment(), event_type=None)

    ordinary = await repo.list_ordinary_screenings()

    assert [event.film.title for event in ordinary] == ["Ordinary"]


async def test_list_films_missing_imdb_id_returns_only_tmdb_enriched_gaps(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    unenriched = await _ingest(repo, title="Unenriched", starts_at=_moment())
    pre_feature = await _ingest(repo, title="Pre-feature", starts_at=_moment())
    pre_feature.film.tmdb_id = 42
    await repo.save_film(pre_feature.film)
    backfilled = await _ingest(repo, title="Backfilled", starts_at=_moment())
    backfilled.film.tmdb_id = 43
    backfilled.film.imdb_id = "tt0000043"
    await repo.save_film(backfilled.film)

    gaps = await repo.list_films_missing_imdb_id()

    assert [f.title for f in gaps] == ["Pre-feature"]
    assert unenriched.film.tmdb_id is None  # sanity: never touched by this query
