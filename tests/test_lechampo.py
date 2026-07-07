"""Tests for the Le Champo scraper (hand-authored CMS article, LLM extraction)."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from cine_event_bot.core.models import EventType, ExtractedEvent, Sighting, Source
from cine_event_bot.core.progress import NullReporter
from cine_event_bot.io.scrapers.lechampo import LeChampoScraper

_FIXTURES = Path(__file__).parent / "fixtures"
_REFERENCE_DATE = date(2026, 6, 20)


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _extractor_returning(event: ExtractedEvent) -> MagicMock:
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=event)
    return extractor


def _sample_extracted() -> ExtractedEvent:
    return ExtractedEvent(
        title="The Swimmer",
        event_type=EventType.CINE_CLUB,
        venue="Le Champo",
        starts_at=datetime.now(UTC) + timedelta(days=1),
    )


def _response(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.raise_for_status = MagicMock()
    return response


def test_parse_listings_skips_cycles_without_an_announced_date() -> None:
    scraper = LeChampoScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lechampo_cineclubs.html"), reference_date=_REFERENCE_DATE
    )

    # Three panels in the fixture: two with a "📍" date, one (Lundis Hongrois)
    # with no upcoming date announced — and the intro panel has no cycle at all.
    assert len(listings) == 2


def test_parse_listings_gathers_venue_cycle_and_pin_date() -> None:
    scraper = LeChampoScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lechampo_cineclubs.html"), reference_date=_REFERENCE_DATE
    )

    bo_listing = next(
        listing for listing in listings if "B.0. Ciné-club" in listing.raw_text
    )
    assert bo_listing.source is Source.LE_CHAMPO
    assert "Le Champo" in bo_listing.raw_text
    assert "Reference date: 2026-06-20" in bo_listing.raw_text
    assert "Jeudi 25 juin à 20h00" in bo_listing.raw_text
    assert "THE SWIMMER" in bo_listing.raw_text


def test_parse_listings_extracts_the_reservation_booking_link() -> None:
    scraper = LeChampoScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lechampo_cineclubs.html"), reference_date=_REFERENCE_DATE
    )

    bo_listing = next(
        listing for listing in listings if "B.0. Ciné-club" in listing.raw_text
    )
    assert bo_listing.booking_url is not None
    assert "media/1388" in bo_listing.booking_url


def test_parse_listings_picks_reservation_link_over_other_links() -> None:
    scraper = LeChampoScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("lechampo_cineclubs.html"), reference_date=_REFERENCE_DATE
    )

    history_listing = next(
        listing for listing in listings if "Rencontres de L" in listing.raw_text
    )
    assert history_listing.booking_url is not None
    assert "media/179" in history_listing.booking_url
    assert "lhistoire.fr" not in history_listing.booking_url


async def test_fetch_events_structures_each_announced_cycle() -> None:
    extractor = _extractor_returning(_sample_extracted())
    scraper = LeChampoScraper(extractor)
    client = MagicMock()
    client.get = AsyncMock(return_value=_response(_fixture("lechampo_cineclubs.html")))

    sightings = await scraper.fetch_events(client, NullReporter())

    assert len(sightings) == 2
    assert all(isinstance(sighting, Sighting) for sighting in sightings)
    assert all(sighting.source is Source.LE_CHAMPO for sighting in sightings)
    assert extractor.extract.await_count == 2


async def test_fetch_events_skips_cycles_whose_extraction_fails() -> None:
    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=ValueError("LLM down"))
    scraper = LeChampoScraper(extractor)
    client = MagicMock()
    client.get = AsyncMock(return_value=_response(_fixture("lechampo_cineclubs.html")))

    sightings = await scraper.fetch_events(client, NullReporter())

    assert sightings == []
