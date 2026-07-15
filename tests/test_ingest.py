"""Tests for the deduplicating ingestion (sighting -> film/venue/event)."""

from datetime import UTC, datetime

from factories import DEFAULT_START, make_sighting
from sqlmodel.ext.asyncio.session import AsyncSession

from lanterne.core.models import Source
from lanterne.io.repository import EventRepository


async def _count(repo: EventRepository) -> int:
    window = await repo.list_between(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)
    )
    return len(window)


async def test_ingest_inserts_a_new_event(session: AsyncSession) -> None:
    repo = EventRepository(session)

    stored = await repo.ingest(
        make_sighting(source_url="https://premiereprojo.fr/dune")
    )

    assert stored.id is not None
    assert await _count(repo) == 1


async def test_ingest_creates_film_and_venue_rows(session: AsyncSession) -> None:
    repo = EventRepository(session)

    stored = await repo.ingest(make_sighting())

    assert stored.film.title == "Dune"
    assert stored.film.title_key == "dune"
    assert stored.venue.name == "Le Grand Rex"
    assert stored.venue.slug == "le grand rex"


async def test_ingest_same_screening_twice_keeps_one_row(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await repo.ingest(make_sighting(source=Source.PREMIERE_PROJO, source_url="a"))

    await repo.ingest(make_sighting(source=Source.FORUM_DES_IMAGES, source_url="b"))

    assert await _count(repo) == 1


async def test_ingest_records_one_sighting_per_source(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    first = await repo.ingest(
        make_sighting(
            source=Source.PREMIERE_PROJO,
            source_url="https://premiereprojo.fr/dune",
        )
    )

    merged = await repo.ingest(
        make_sighting(
            source=Source.FORUM_DES_IMAGES,
            source_url="https://forumdesimages.fr/dune",
        )
    )

    assert merged.id == first.id
    assert first.id is not None
    sightings = await repo.list_sightings(first.id)
    assert {(s.source, s.source_url) for s in sightings} == {
        (Source.PREMIERE_PROJO, "https://premiereprojo.fr/dune"),
        (Source.FORUM_DES_IMAGES, "https://forumdesimages.fr/dune"),
    }


async def test_ingest_rescraping_same_source_keeps_one_sighting(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    stored = await repo.ingest(make_sighting(source_url="a"))

    await repo.ingest(make_sighting(source_url="a"))

    assert stored.id is not None
    assert len(await repo.list_sightings(stored.id)) == 1


async def test_ingest_merges_team_presence_with_logical_or(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    await repo.ingest(make_sighting(has_team_present=False))

    merged = await repo.ingest(
        make_sighting(source=Source.FORUM_DES_IMAGES, has_team_present=True)
    )

    assert merged.has_team_present is True


async def test_ingest_fills_missing_description(session: AsyncSession) -> None:
    repo = EventRepository(session)
    await repo.ingest(make_sighting(description=None))

    merged = await repo.ingest(
        make_sighting(
            source=Source.FORUM_DES_IMAGES,
            description="En présence du réalisateur.",
        )
    )

    assert merged.description == "En présence du réalisateur."


async def test_ingest_keeps_existing_description(session: AsyncSession) -> None:
    repo = EventRepository(session)
    await repo.ingest(make_sighting(description="Original."))

    merged = await repo.ingest(
        make_sighting(source=Source.FORUM_DES_IMAGES, description="Replacement.")
    )

    assert merged.description == "Original."


async def test_ingest_backfills_cycle_and_booking(session: AsyncSession) -> None:
    repo = EventRepository(session)
    await repo.ingest(make_sighting(cycle_name=None, booking_url=None))

    merged = await repo.ingest(
        make_sighting(
            source=Source.CINEMATHEQUE,
            cycle_name="Rétrospective Lynch",
            booking_url="https://tickets.example/dune",
        )
    )

    assert merged.cycle_name == "Rétrospective Lynch"
    assert merged.booking_url == "https://tickets.example/dune"


async def test_ingest_shares_the_film_across_screenings(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    first = await repo.ingest(make_sighting(starts_at=DEFAULT_START))

    second = await repo.ingest(
        make_sighting(starts_at=datetime(2026, 7, 3, 20, 30, tzinfo=UTC))
    )

    assert second.id != first.id
    assert second.film_id == first.film_id


async def test_ingest_resolves_venue_case_insensitively(
    session: AsyncSession,
) -> None:
    repo = EventRepository(session)
    first = await repo.ingest(make_sighting(venue="Le Grand Rex"))

    second = await repo.ingest(
        make_sighting(
            venue="le  grand REX",
            starts_at=datetime(2026, 7, 3, 20, 30, tzinfo=UTC),
        )
    )

    assert second.venue_id == first.venue_id
