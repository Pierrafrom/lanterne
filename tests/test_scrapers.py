"""Tests for the scraper architecture and the Cinémathèque scraper."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from cine_event_bot.core.models import EventType, ExtractedEvent, Sighting, Source
from cine_event_bot.core.progress import NullReporter
from cine_event_bot.io.scrapers import CinemathequeScraper, build_scrapers
from cine_event_bot.io.scrapers.base import SourceScraper

_FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _extractor_returning(event: ExtractedEvent) -> MagicMock:
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=event)
    return extractor


def _sample_extracted() -> ExtractedEvent:
    return ExtractedEvent(
        title="Ciao, professore!",
        event_type=EventType.RETROSPECTIVE,
        venue="La Cinémathèque française",
        # Relative to now: structure_via_llm rejects implausible dates.
        starts_at=datetime.now(UTC) + timedelta(days=14),
    )


def _response(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.raise_for_status = MagicMock()
    return response


def test_registry_covers_every_source_when_fully_configured() -> None:
    scrapers = build_scrapers(
        _extractor_returning(_sample_extracted()),
        paris_cine_info_login="user@example.test",
        paris_cine_info_password="secret",
    )

    assert {scraper.source for scraper in scrapers} == set(Source)


def test_registry_skips_paris_cine_info_without_credentials() -> None:
    scrapers = build_scrapers(_extractor_returning(_sample_extracted()))

    assert Source.PARIS_CINE_INFO not in {scraper.source for scraper in scrapers}


def test_every_built_scraper_satisfies_the_protocol() -> None:
    scrapers = build_scrapers(
        _extractor_returning(_sample_extracted()),
        paris_cine_info_login="user@example.test",
        paris_cine_info_password="secret",
    )

    assert all(isinstance(scraper, SourceScraper) for scraper in scrapers)


def test_parse_index_extracts_detail_urls_in_order() -> None:
    scraper = CinemathequeScraper(_extractor_returning(_sample_extracted()))

    urls = scraper.parse_index(_fixture("cinematheque_index.html"))

    assert urls == [
        "https://www.cinematheque.fr/seance/45406.html",
        "https://www.cinematheque.fr/seance/45428.html",
    ]


def test_parse_detail_gathers_venue_cycle_date_and_film() -> None:
    scraper = CinemathequeScraper(_extractor_returning(_sample_extracted()))

    listing = scraper.parse_detail(
        _fixture("cinematheque_seance.html"),
        "https://www.cinematheque.fr/seance/45406.html",
    )

    assert listing.source is Source.CINEMATHEQUE
    assert listing.source_url == "https://www.cinematheque.fr/seance/45406.html"
    assert "La Cinémathèque française" in listing.raw_text
    assert "Rétrospective Lina Wertmüller" in listing.raw_text
    assert "jeudi 25 juin 2026, 18h30" in listing.raw_text
    assert "Ciao, professore!" in listing.raw_text


async def test_fetch_events_structures_each_listing_into_a_sighting() -> None:
    extractor = _extractor_returning(_sample_extracted())
    scraper = CinemathequeScraper(extractor)
    detail_html = _fixture("cinematheque_seance.html")
    client = MagicMock()
    client.get = AsyncMock(
        side_effect=[
            _response(_fixture("cinematheque_index.html")),
            _response(detail_html),
            _response(detail_html),
        ]
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert len(sightings) == 2
    assert all(isinstance(sighting, Sighting) for sighting in sightings)
    assert sightings[0].source is Source.CINEMATHEQUE
    assert sightings[0].extracted.title == "Ciao, professore!"
    assert extractor.extract.await_count == 2


async def test_fetch_events_skips_listings_whose_extraction_fails() -> None:
    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=ValueError("LLM down"))
    scraper = CinemathequeScraper(extractor)
    detail_html = _fixture("cinematheque_seance.html")
    client = MagicMock()
    client.get = AsyncMock(
        side_effect=[
            _response(_fixture("cinematheque_index.html")),
            _response(detail_html),
            _response(detail_html),
        ]
    )

    events = await scraper.fetch_events(client, NullReporter())

    assert events == []


async def test_fetch_events_drops_implausible_extractions() -> None:
    # A hallucinated past date must be rejected by the validation guards.
    stale = ExtractedEvent(
        title="Ciao, professore!",
        event_type=EventType.RETROSPECTIVE,
        venue="La Cinémathèque française",
        starts_at=datetime.now(UTC) - timedelta(days=30),
    )
    scraper = CinemathequeScraper(_extractor_returning(stale))
    detail_html = _fixture("cinematheque_seance.html")
    client = MagicMock()
    client.get = AsyncMock(
        side_effect=[
            _response(_fixture("cinematheque_index.html")),
            _response(detail_html),
            _response(detail_html),
        ]
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert sightings == []
