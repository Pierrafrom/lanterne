"""Tests for the Première Projo scraper (embedded RSC JSON, no LLM)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from cine_event_bot.core.models import EventType, ScreeningEvent, Source
from cine_event_bot.core.progress import NullReporter
from cine_event_bot.io.scrapers.premiereprojo import PremiereProjoScraper

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


def _by_title(events: list[ScreeningEvent]) -> dict[str, ScreeningEvent]:
    return {event.title: event for event in events}


def test_parse_events_maps_each_show_to_an_event() -> None:
    scraper = PremiereProjoScraper()

    events = scraper.parse_events(_fixture("premiereprojo_home.html"))

    assert {event.title for event in events} == {"Tempura", "Agon"}
    assert all(event.source is Source.PREMIERE_PROJO for event in events)
    assert all(event.event_type is EventType.AVANT_PREMIERE for event in events)


def test_parse_events_flags_team_presence_from_avpe() -> None:
    scraper = PremiereProjoScraper()

    events = _by_title(scraper.parse_events(_fixture("premiereprojo_home.html")))

    assert events["Tempura"].has_team_present is True  # avpType AVPE
    assert events["Agon"].has_team_present is False  # avpType AVP


def test_parse_events_converts_start_time_to_utc() -> None:
    scraper = PremiereProjoScraper()

    events = _by_title(scraper.parse_events(_fixture("premiereprojo_home.html")))

    # 2026-07-07T16:30:00+02:00 -> 14:30 UTC
    assert events["Tempura"].starts_at == datetime(2026, 7, 7, 14, 30, tzinfo=UTC)


def test_parse_events_uses_cinema_name_and_ticket_link() -> None:
    scraper = PremiereProjoScraper()

    events = _by_title(scraper.parse_events(_fixture("premiereprojo_home.html")))

    assert events["Tempura"].venue == "MK2 Bibliothèque"
    assert events["Tempura"].source_url is not None
    assert "sessionId=136127" in events["Tempura"].source_url


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

    events = scraper.parse_events(_rsc_html(state))

    assert [event.title for event in events] == ["Valid"]


def test_parse_events_ignores_unrelated_data_arrays() -> None:
    scraper = PremiereProjoScraper()
    state = {
        "other": {"data": [{"id": 1}, {"id": 2}]},  # not movies (no "shows")
        "state": {"data": [{"title": "Valid", "shows": [_show()]}]},
    }

    events = scraper.parse_events(_rsc_html(state))

    assert [event.title for event in events] == ["Valid"]


async def test_fetch_events_reads_the_homepage() -> None:
    scraper = PremiereProjoScraper()
    response = MagicMock()
    response.text = _fixture("premiereprojo_home.html")
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    events = await scraper.fetch_events(client, NullReporter())

    assert len(events) == 2
    client.get.assert_awaited_once()
