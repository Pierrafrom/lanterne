"""Tests for the La Villette open-air cinema scraper (day-grouped page)."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from cine_event_bot.core.models import EventType, ExtractedEvent, Sighting, Source
from cine_event_bot.core.progress import NullReporter
from cine_event_bot.io.scrapers.lavillette import LaVilletteScraper

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
        listing for listing in listings if "Mon voisin Totoro" in listing.raw_text
    )
    assert "Mercredi 22 juillet à 18h00" in totoro.raw_text
    assert "Reference date: 2026-07-06" in totoro.raw_text
    assert "Cinéma en plein air de La Villette" in totoro.raw_text
    assert totoro.source is Source.LA_VILLETTE


def test_parse_listings_flags_main_feature_as_21h() -> None:
    scraper = LaVilletteScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lavillette_plein_air.html"), reference_date=_REFERENCE_DATE
    )

    regne = next(
        listing for listing in listings if "Le Règne animal" in listing.raw_text
    )
    assert "Mercredi 22 juillet à 21h00" in regne.raw_text


def test_parse_listings_defaults_a_single_daily_film_to_21h() -> None:
    scraper = LaVilletteScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lavillette_plein_air.html"), reference_date=_REFERENCE_DATE
    )

    nouveau_monde = next(
        listing for listing in listings if "Le Nouveau Monde" in listing.raw_text
    )
    assert "Jeudi 23 juillet à 21h00" in nouveau_monde.raw_text


def test_parse_listings_does_not_bleed_titles_across_films() -> None:
    scraper = LaVilletteScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lavillette_plein_air.html"), reference_date=_REFERENCE_DATE
    )

    totoro = next(
        listing for listing in listings if "Mon voisin Totoro" in listing.raw_text
    )
    assert "Le Règne animal" not in totoro.raw_text


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
