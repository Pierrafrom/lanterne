"""Tests for the Première Projo scraper (embedded RSC JSON, no LLM)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from lanterne.core.models import EventType, Sighting, Source
from lanterne.core.progress import NullReporter
from lanterne.io.scrapers.premiereprojo import PremiereProjoScraper

_FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _rsc_html(state: dict[str, Any]) -> str:
    """Wrap a React Query state object into a minimal RSC-bearing HTML page."""
    chunk = json.dumps("8:" + json.dumps(state, separators=(",", ":")))
    return f"<html><body><script>self.__next_f.push([1,{chunk}])</script></body></html>"


def _show(**overrides: Any) -> dict[str, Any]:
    show = {
        "date": "2026-07-07T16:30:00+02:00",
        "avpType": "AVP",
        "cinemas": {"name": "MK2 Bibliothèque"},
        "linkShow": "https://example.test/s",
    }
    show.update(overrides)
    return show


def _by_title(sightings: list[Sighting]) -> dict[str, Sighting]:
    return {sighting.extracted.title: sighting for sighting in sightings}


def test_parse_events_maps_each_show_to_a_sighting() -> None:
    scraper = PremiereProjoScraper()

    sightings = scraper.parse_events(_fixture("premiereprojo_home.html"))

    assert {s.extracted.title for s in sightings} == {"Tempura", "Agon"}
    assert all(s.source is Source.PREMIERE_PROJO for s in sightings)
    assert all(s.extracted.event_type is EventType.AVANT_PREMIERE for s in sightings)


def test_parse_events_flags_team_presence_from_avpe() -> None:
    scraper = PremiereProjoScraper()

    sightings = _by_title(scraper.parse_events(_fixture("premiereprojo_home.html")))

    assert sightings["Tempura"].extracted.has_team_present is True  # avpType AVPE
    assert sightings["Agon"].extracted.has_team_present is False  # avpType AVP


def test_parse_events_converts_start_time_to_utc() -> None:
    scraper = PremiereProjoScraper()

    sightings = _by_title(scraper.parse_events(_fixture("premiereprojo_home.html")))

    # 2026-07-07T16:30:00+02:00 -> 14:30 UTC
    assert sightings["Tempura"].extracted.starts_at == datetime(
        2026, 7, 7, 14, 30, tzinfo=UTC
    )


def test_parse_events_uses_cinema_name_and_ticket_link() -> None:
    scraper = PremiereProjoScraper()

    sightings = _by_title(scraper.parse_events(_fixture("premiereprojo_home.html")))

    assert sightings["Tempura"].extracted.venue == "MK2 Bibliothèque"
    assert sightings["Tempura"].source_url is not None
    assert "sessionId=136127" in sightings["Tempura"].source_url


def test_parse_events_returns_empty_when_payload_absent() -> None:
    scraper = PremiereProjoScraper()

    assert scraper.parse_events("<html><body>no payload</body></html>") == []


def test_parse_events_skips_shows_with_missing_or_invalid_fields() -> None:
    scraper = PremiereProjoScraper()
    state = {
        "state": {
            "data": [
                {"title": "No date", "shows": [_show(date=None)]},
                {"title": "Bad date", "shows": [_show(date="not-a-date")]},
                {"title": "No venue", "shows": [_show(cinemas={})]},
                {"shows": [_show()]},  # missing title
                {"title": "Valid", "shows": [_show()]},
            ]
        }
    }

    sightings = scraper.parse_events(_rsc_html(state))

    assert [s.extracted.title for s in sightings] == ["Valid"]


def test_parse_events_ignores_unrelated_data_arrays() -> None:
    scraper = PremiereProjoScraper()
    state = {
        "other": {"data": [{"id": 1}, {"id": 2}]},  # not movies (no "shows")
        "state": {"data": [{"title": "Valid", "shows": [_show()]}]},
    }

    sightings = scraper.parse_events(_rsc_html(state))

    assert [s.extracted.title for s in sightings] == ["Valid"]


async def test_fetch_events_reads_the_homepage() -> None:
    scraper = PremiereProjoScraper()
    response = MagicMock()
    response.text = _fixture("premiereprojo_home.html")
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    sightings = await scraper.fetch_events(client, NullReporter())

    assert len(sightings) == 2
    client.get.assert_awaited_once()
