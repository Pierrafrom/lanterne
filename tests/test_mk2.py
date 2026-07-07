"""Tests for the MK2 scraper (embedded RSC JSON, no LLM)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from cine_event_bot.core.models import Sighting, Source
from cine_event_bot.core.progress import NullReporter
from cine_event_bot.io.scrapers.mk2 import Mk2Scraper

_FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _rsc_html(payload: list[dict[str, Any]]) -> str:
    """Wrap a list of event-list objects into a minimal RSC-bearing HTML page."""
    decoded = "3:" + json.dumps(payload, separators=(",", ":"))
    chunk = json.dumps("8:" + decoded)
    return f"<html><body><script>self.__next_f.push([1,{chunk}])</script></body></html>"


def _event_list(**overrides: Any) -> dict[str, Any]:
    event_list = {
        "slug": "avant-premieres-avec-equipe",
        "type": "event-list",
        "title": "AVANT-PREMIÈRES AVEC ÉQUIPE",
        "events": [_event()],
    }
    event_list.update(overrides)
    return event_list


def _event(**overrides: Any) -> dict[str, Any]:
    event = {
        "name": "Test Film",
        "slug": "test-film",
        "type": {"id": "avant-premiere", "name": "Avant-première"},
        "genres": [{"id": "equipe-du-film", "name": "Équipe du film"}],
        "description": "Une description.",
        "nextSession": {"cinemaId": "0011", "showTime": "2026-07-07T18:00:00.000Z"},
        "linkedCinemas": [{"id": "0011", "name": "quai de seine"}],
    }
    event.update(overrides)
    return event


def test_parse_events_maps_a_real_fixture_avant_premiere_and_festival() -> None:
    scraper = Mk2Scraper()

    sightings = scraper.parse_events(_fixture("mk2_evenements.html"))

    titles = {s.extracted.title for s in sightings}
    assert "L'Écologie des sentiments" in titles
    assert "Festival : 10 ans de labels mk2 !" in titles
    assert len(sightings) == 2  # cinema-club and conferences entries excluded


def test_parse_events_flags_team_presence_from_genre_tag() -> None:
    scraper = Mk2Scraper()

    sightings = scraper.parse_events(_fixture("mk2_evenements.html"))

    ecologie = next(
        s for s in sightings if s.extracted.title == "L'Écologie des sentiments"
    )
    assert ecologie.extracted.has_team_present is True


def test_parse_events_resolves_venue_from_linked_cinemas() -> None:
    scraper = Mk2Scraper()

    sightings = scraper.parse_events(_fixture("mk2_evenements.html"))

    ecologie = next(
        s for s in sightings if s.extracted.title == "L'Écologie des sentiments"
    )
    assert ecologie.extracted.venue == "mk2 quai de seine"


def test_parse_events_builds_the_per_event_url() -> None:
    scraper = Mk2Scraper()

    sightings = scraper.parse_events(_fixture("mk2_evenements.html"))

    ecologie = next(
        s for s in sightings if s.extracted.title == "L'Écologie des sentiments"
    )
    assert (
        ecologie.source_url
        == "https://www.mk2.com/evenements/l-ecologie-des-sentiments"
    )
    assert ecologie.source is Source.MK2


def test_parse_events_converts_showtime_to_utc() -> None:
    scraper = Mk2Scraper()

    sightings = scraper.parse_events(_rsc_html([_event_list(events=[_event()])]))

    assert sightings[0].extracted.starts_at == datetime(2026, 7, 7, 18, 0, tzinfo=UTC)


def test_parse_events_skips_cinema_club_and_conference_types() -> None:
    scraper = Mk2Scraper()
    payload = [
        _event_list(
            slug="cinema-clubs",
            events=[_event(type={"id": "cinema-club", "name": "Cinéma club"})],
        ),
        _event_list(
            slug="cycles-de-conferences",
            events=[_event(type={"id": "conferences", "name": "Conférences"})],
        ),
    ]

    sightings = scraper.parse_events(_rsc_html(payload))

    assert sightings == []


def test_parse_events_leaves_team_presence_false_without_the_genre_tag() -> None:
    scraper = Mk2Scraper()

    sightings = scraper.parse_events(
        _rsc_html([_event_list(events=[_event(genres=[])])])
    )

    assert sightings[0].extracted.has_team_present is False


def test_parse_events_falls_back_to_first_cinema_when_no_id_match() -> None:
    scraper = Mk2Scraper()

    sightings = scraper.parse_events(
        _rsc_html(
            [
                _event_list(
                    events=[
                        _event(
                            nextSession={
                                "cinemaId": "9999",
                                "showTime": "2026-07-07T18:00:00.000Z",
                            },
                            linkedCinemas=[{"id": "0011", "name": "quai de seine"}],
                        )
                    ]
                )
            ]
        )
    )

    assert sightings[0].extracted.venue == "mk2 quai de seine"


def test_parse_events_skips_malformed_events() -> None:
    scraper = Mk2Scraper()
    payload = [
        _event_list(events=[_event(name=None)]),
        _event_list(events=[_event(nextSession={})]),
        _event_list(events=[_event(linkedCinemas=[])]),
        _event_list(events=[_event(type={"id": "unknown-type"})]),
        _event_list(events=[_event()]),
    ]

    sightings = scraper.parse_events(_rsc_html(payload))

    assert len(sightings) == 1


def test_parse_events_returns_empty_when_payload_absent() -> None:
    scraper = Mk2Scraper()

    assert scraper.parse_events("<html><body>no payload</body></html>") == []


async def test_fetch_events_reads_the_events_page() -> None:
    scraper = Mk2Scraper()
    response = MagicMock()
    response.text = _fixture("mk2_evenements.html")
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    sightings = await scraper.fetch_events(client, NullReporter())

    assert len(sightings) == 2
    assert all(isinstance(sighting, Sighting) for sighting in sightings)
    client.get.assert_awaited_once()
