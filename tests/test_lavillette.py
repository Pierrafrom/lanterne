"""Tests for the La Villette open-air cinema scraper (day-grouped page)."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from lanterne.core.models import EventType, ExtractedEvent, Sighting, Source
from lanterne.core.progress import NullReporter
from lanterne.io.scrapers.lavillette import (
    LaVilletteScraper,
    _resolve_known_starts_at,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_REFERENCE_DATE = date(2026, 7, 6)


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _extractor_returning(event: ExtractedEvent) -> MagicMock:
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=event)
    return extractor


def _sample_extracted() -> ExtractedEvent:
    return ExtractedEvent(
        title="Mon voisin Totoro",
        event_type=EventType.OPEN_AIR,
        venue="Cinéma en plein air de La Villette",
        starts_at=datetime.now(UTC) + timedelta(days=1),
    )


def _response(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.raise_for_status = MagicMock()
    return response


def test_parse_listings_finds_every_film_across_both_days() -> None:
    scraper = LaVilletteScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lavillette_plein_air.html"), reference_date=_REFERENCE_DATE
    )

    # Wednesday has 2 films, Thursday has 1 -> 3 total.
    assert len(listings) == 3


def test_parse_listings_flags_young_audience_screening_as_18h() -> None:
    scraper = LaVilletteScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lavillette_plein_air.html"), reference_date=_REFERENCE_DATE
    )

    totoro = next(
        listing
        for listing in listings
        if "Mon voisin Totoro" in listing.listing.raw_text
    )
    assert "Mercredi 22 juillet à 18h00" in totoro.listing.raw_text
    assert "Reference date: 2026-07-06" in totoro.listing.raw_text
    assert "Cinéma en plein air de La Villette" in totoro.listing.raw_text
    assert totoro.listing.source is Source.LA_VILLETTE
    assert totoro.known_starts_at == datetime(2026, 7, 22, 16, 0, tzinfo=UTC)


def test_parse_listings_flags_main_feature_as_21h() -> None:
    scraper = LaVilletteScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lavillette_plein_air.html"), reference_date=_REFERENCE_DATE
    )

    regne = next(
        listing for listing in listings if "Le Règne animal" in listing.listing.raw_text
    )
    assert "Mercredi 22 juillet à 21h00" in regne.listing.raw_text
    assert regne.known_starts_at == datetime(2026, 7, 22, 19, 0, tzinfo=UTC)


def test_parse_listings_defaults_a_single_daily_film_to_21h() -> None:
    scraper = LaVilletteScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lavillette_plein_air.html"), reference_date=_REFERENCE_DATE
    )

    nouveau_monde = next(
        listing
        for listing in listings
        if "Le Nouveau Monde" in listing.listing.raw_text
    )
    assert "Jeudi 23 juillet à 21h00" in nouveau_monde.listing.raw_text
    assert nouveau_monde.known_starts_at == datetime(2026, 7, 23, 19, 0, tzinfo=UTC)


def test_parse_listings_does_not_bleed_titles_across_films() -> None:
    scraper = LaVilletteScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lavillette_plein_air.html"), reference_date=_REFERENCE_DATE
    )

    totoro = next(
        listing
        for listing in listings
        if "Mon voisin Totoro" in listing.listing.raw_text
    )
    assert "Le Règne animal" not in totoro.listing.raw_text


async def test_fetch_events_structures_every_film() -> None:
    extractor = _extractor_returning(_sample_extracted())
    scraper = LaVilletteScraper(extractor)
    client = MagicMock()
    client.get = AsyncMock(
        return_value=_response(_fixture("lavillette_plein_air.html"))
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert len(sightings) == 3
    assert all(isinstance(sighting, Sighting) for sighting in sightings)
    assert all(sighting.source is Source.LA_VILLETTE for sighting in sightings)


def test_resolve_known_starts_at_returns_none_on_an_unmatched_heading() -> None:
    assert _resolve_known_starts_at("not a day heading", 21, 0, _REFERENCE_DATE) is None


def test_resolve_known_starts_at_returns_none_on_an_invalid_day_month() -> None:
    assert _resolve_known_starts_at("Lundi 30 février", 21, 0, _REFERENCE_DATE) is None


async def test_fetch_events_known_starts_at_wins_over_a_wrong_llm_guess() -> None:
    # Regression case: the LLM resolves the year-less day heading to
    # something plausible-looking but wrong (or even in the past, exactly
    # what broke the now-retired lechampo.py) — the known, deterministically
    # resolved date must win regardless of what the LLM returns.
    wrong_guess = ExtractedEvent(
        title="Mon voisin Totoro",
        event_type=EventType.OPEN_AIR,
        venue="Cinéma en plein air de La Villette",
        starts_at=datetime(2020, 1, 1, tzinfo=UTC),  # clearly wrong, in the past
    )
    extractor = _extractor_returning(wrong_guess)
    scraper = LaVilletteScraper(extractor)
    client = MagicMock()
    client.get = AsyncMock(
        return_value=_response(_fixture("lavillette_plein_air.html"))
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert len(sightings) == 3
    assert all(
        sighting.extracted.starts_at != datetime(2020, 1, 1, tzinfo=UTC)
        for sighting in sightings
    )


async def test_fetch_events_skips_films_whose_extraction_fails() -> None:
    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=ValueError("LLM down"))
    scraper = LaVilletteScraper(extractor)
    client = MagicMock()
    client.get = AsyncMock(
        return_value=_response(_fixture("lavillette_plein_air.html"))
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert sightings == []
