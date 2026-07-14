"""Tests for the offi.fr scraper (Île-de-France suburb complement)."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx

from cine_event_bot.core.models import Source
from cine_event_bot.core.progress import NullReporter
from cine_event_bot.io.scrapers.offi import (
    OffiScraper,
    _max_page,
    _parse_venue_urls,
    parse_venue_page,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _response(text: str = "") -> MagicMock:
    response = MagicMock()
    response.text = text
    response.raise_for_status = MagicMock()
    return response


class TestMaxPage:
    def test_reads_the_highest_pagination_page(self) -> None:
        assert _max_page(_fixture("offi_department_listing.html")) == 2

    def test_defaults_to_one_without_a_pager(self) -> None:
        assert _max_page("<html></html>") == 1


class TestParseVenueUrls:
    def test_extracts_every_venue_link(self) -> None:
        urls = _parse_venue_urls(_fixture("offi_department_listing.html"))

        assert urls == [
            "https://www.offi.fr/cinema/cine-104-1812.html",
            "https://www.offi.fr/cinema/ugc-cine-cite-noisy-3318.html",
        ]


class TestParseVenuePage:
    def test_extracts_one_sighting_per_showtime_across_day_tabs(self) -> None:
        sightings = parse_venue_page(
            _fixture("offi_venue_page.html"), reference_date=date(2026, 7, 13)
        )

        assert len(sightings) == 3
        assert all(sighting.source is Source.OFFI for sighting in sightings)
        assert all(sighting.extracted.event_type is None for sighting in sightings)
        titles = {sighting.extracted.title for sighting in sightings}
        assert titles == {"La Bataille de Gaulle : L'Âge de fer", "La Chaleur"}

    def test_computes_the_first_tabs_date_as_the_reference_date(self) -> None:
        # 14:00 Paris local in July (summer, UTC+2) -> 12:00 UTC.
        sightings = parse_venue_page(
            _fixture("offi_venue_page.html"), reference_date=date(2026, 7, 13)
        )

        first_day = next(
            s for s in sightings if s.extracted.title.startswith("La Bataille")
        )
        assert first_day.extracted.starts_at == datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

    def test_computes_later_tabs_as_one_day_after_the_previous_one(self) -> None:
        sightings = parse_venue_page(
            _fixture("offi_venue_page.html"), reference_date=date(2026, 7, 13)
        )

        second_day = {
            s.extracted.starts_at
            for s in sightings
            if s.extracted.title == "La Chaleur"
        }
        assert second_day == {
            datetime(2026, 7, 14, 12, 15, tzinfo=UTC),
            datetime(2026, 7, 14, 18, 45, tzinfo=UTC),
        }

    def test_skips_a_movie_tile_with_no_showtimes(self) -> None:
        sightings = parse_venue_page(
            _fixture("offi_venue_page.html"), reference_date=date(2026, 7, 13)
        )

        assert "Séance annulée" not in {s.extracted.title for s in sightings}

    def test_returns_nothing_without_a_venue_name(self) -> None:
        sightings = parse_venue_page(
            "<html><body></body></html>", reference_date=date.today()
        )

        assert sightings == []


async def test_fetch_events_discovers_venues_then_scrapes_each_programme() -> None:
    scraper = OffiScraper()
    department_html = _fixture("offi_department_listing.html")
    venue_html = _fixture("offi_venue_page.html")

    async def fake_get(url: str, params: dict[str, Any] | None = None) -> MagicMock:
        if url.endswith("cine-104-1812.html"):
            return _response(venue_html)
        if url.endswith("ugc-cine-cite-noisy-3318.html"):
            return _response('<h1 itemprop="name"></h1>')
        if params and params.get("npage") == 2:
            return _response("")
        return _response(department_html)

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)

    sightings = await scraper.fetch_events(client, NullReporter())

    # Every department fixture (all seven slugs) lists the same two venues,
    # so the URL set dedups down to 2 distinct venue-page fetches regardless
    # of how many departments and pagination pages report them.
    assert len(sightings) == 3
    assert all(sighting.source is Source.OFFI for sighting in sightings)


async def test_fetch_events_skips_a_venue_page_that_fails_to_load() -> None:
    scraper = OffiScraper()
    department_html = _fixture("offi_department_listing.html")

    async def fake_get(url: str, params: dict[str, Any] | None = None) -> MagicMock:
        if url.endswith("cine-104-1812.html"):
            raise httpx.ConnectError("boom")
        if url.endswith("ugc-cine-cite-noisy-3318.html"):
            return _response('<h1 itemprop="name"></h1>')
        if params and params.get("npage") == 2:
            return _response("")
        return _response(department_html)

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)

    sightings = await scraper.fetch_events(client, NullReporter())

    assert sightings == []


async def test_fetch_events_skips_a_department_page_that_fails_to_load() -> None:
    scraper = OffiScraper()
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))

    sightings = await scraper.fetch_events(client, NullReporter())

    assert sightings == []
