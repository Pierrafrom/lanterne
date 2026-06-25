"""Tests for the scraper architecture and the Cinémathèque scraper."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from cine_event_bot.core.models import Source
from cine_event_bot.io.scrapers import SCRAPERS, CinemathequeScraper, PendingScraper
from cine_event_bot.io.scrapers.base import RawListing, SourceScraper

_FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_registry_covers_the_four_sources() -> None:
    covered = {scraper.source for scraper in SCRAPERS}

    assert covered == set(Source)


def test_every_registered_scraper_satisfies_the_protocol() -> None:
    assert all(isinstance(scraper, SourceScraper) for scraper in SCRAPERS)


def test_parse_index_extracts_detail_urls_in_order() -> None:
    scraper = CinemathequeScraper()

    urls = scraper.parse_index(_fixture("cinematheque_index.html"))

    assert urls == [
        "https://www.cinematheque.fr/seance/45406.html",
        "https://www.cinematheque.fr/seance/45428.html",
    ]


def test_parse_detail_gathers_venue_cycle_date_and_film() -> None:
    scraper = CinemathequeScraper()

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


async def test_fetch_listings_walks_index_then_each_detail() -> None:
    scraper = CinemathequeScraper()
    index_html = _fixture("cinematheque_index.html")
    detail_html = _fixture("cinematheque_seance.html")
    responses = [_response(index_html), _response(detail_html), _response(detail_html)]
    client = MagicMock()
    client.get = AsyncMock(side_effect=responses)

    listings = await scraper.fetch_listings(client)

    assert len(listings) == 2
    assert all(isinstance(item, RawListing) for item in listings)
    assert client.get.await_count == 3


async def test_pending_scraper_yields_nothing() -> None:
    scraper = PendingScraper(Source.PREMIERE_PROJO)

    listings = await scraper.fetch_listings(MagicMock())

    assert listings == []
    assert scraper.source is Source.PREMIERE_PROJO


def _response(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.raise_for_status = MagicMock()
    return response
