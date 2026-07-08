"""Tests for the Paris Ciné Info scraper (authenticated JSON API)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cine_event_bot.core.models import EventType, ExtractedEvent, Sighting, Source
from cine_event_bot.core.progress import NullReporter
from cine_event_bot.io.scrapers.paris_cine_info import (
    ParisCineInfoScraper,
    build_candidate,
)


def _movie(**overrides: Any) -> dict[str, Any]:
    movie = {"id": 42, "ti": "Häxan", "la": "?"}
    movie.update(overrides)
    return movie


def _showtime(**overrides: Any) -> dict[str, Any]:
    showtime = {
        "title": "Le Grand Action",
        "start": "2026-07-08T20:00:00",
        "com": "Avant-première en présence de la réalisatrice.",
        "book": "https://example.test/booking/123",
    }
    showtime.update(overrides)
    return showtime


def _extractor_returning(event: ExtractedEvent) -> MagicMock:
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=event)
    return extractor


def _sample_extracted() -> ExtractedEvent:
    from datetime import timedelta

    return ExtractedEvent(
        title="Häxan",
        event_type=EventType.AVANT_PREMIERE,
        venue="Le Grand Action",
        starts_at=datetime.now(UTC) + timedelta(days=1),
        has_team_present=True,
    )


class TestBuildCandidate:
    def test_builds_a_candidate_from_a_commented_showtime(self) -> None:
        candidate = build_candidate(_movie(), _showtime())

        assert candidate is not None
        assert candidate.listing.source is Source.PARIS_CINE_INFO
        assert "Le Grand Action" in candidate.listing.raw_text
        assert "Häxan" in candidate.listing.raw_text
        assert (
            "Avant-première en présence de la réalisatrice."
            in candidate.listing.raw_text
        )

    def test_carries_the_known_true_facts_separately_from_the_llm_input(self) -> None:
        candidate = build_candidate(_movie(), _showtime())

        assert candidate is not None
        assert candidate.known_title == "Häxan"
        assert candidate.known_venue == "Le Grand Action"

    def test_uses_the_booking_link_as_source_url_and_booking_url(self) -> None:
        candidate = build_candidate(_movie(), _showtime())

        assert candidate is not None
        assert candidate.listing.source_url == "https://example.test/booking/123"
        assert candidate.listing.booking_url == "https://example.test/booking/123"

    def test_falls_back_to_the_site_url_without_a_booking_link(self) -> None:
        candidate = build_candidate(_movie(), _showtime(book=""))

        assert candidate is not None
        assert candidate.listing.source_url == "https://paris-cine.info/"
        assert candidate.listing.booking_url is None

    def test_returns_none_without_a_comment(self) -> None:
        assert build_candidate(_movie(), _showtime(com="")) is None
        assert build_candidate(_movie(), _showtime(com=None)) is None
        assert build_candidate(_movie(), _showtime(com="   ")) is None

    def test_returns_none_with_missing_essential_fields(self) -> None:
        assert build_candidate(_movie(ti=None), _showtime()) is None
        assert build_candidate(_movie(), _showtime(title=None)) is None
        assert build_candidate(_movie(), _showtime(start="not-a-date")) is None

    def test_converts_paris_local_time_to_utc(self) -> None:
        # 20:00 Paris local in July (summer, UTC+2) -> 18:00 UTC.
        candidate = build_candidate(_movie(), _showtime(start="2026-07-08T20:00:00"))

        assert candidate is not None
        assert candidate.known_starts_at == datetime(2026, 7, 8, 18, 0, tzinfo=UTC)


class _FakeResponse:
    def __init__(self, *, text: str = "", json_data: Any = None) -> None:
        self.text = text
        self._json_data = json_data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._json_data


async def test_fetch_events_logs_in_then_structures_commented_showtimes() -> None:
    extractor = _extractor_returning(_sample_extracted())
    scraper = ParisCineInfoScraper(extractor, "user@example.test", "secret")
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(text="login_success"))
    client.get = AsyncMock(
        side_effect=[
            _FakeResponse(json_data={"data": [_movie()]}),
            _FakeResponse(
                json_data={
                    "showtimes": [_showtime(), _showtime(com="", title="No comment")]
                }
            ),
        ]
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert len(sightings) == 1
    assert isinstance(sightings[0], Sighting)
    assert sightings[0].source is Source.PARIS_CINE_INFO
    client.post.assert_awaited_once()
    assert client.post.call_args.kwargs["data"]["email"] == "user@example.test"
    assert client.post.call_args.kwargs["data"]["password"] == "secret"


async def test_fetch_events_raises_when_login_is_rejected() -> None:
    scraper = ParisCineInfoScraper(
        _extractor_returning(_sample_extracted()), "user@example.test", "wrong"
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(text="bad_password"))

    with pytest.raises(RuntimeError, match="login"):
        await scraper.fetch_events(client, NullReporter())


async def test_fetch_events_discards_the_llms_title_venue_and_time_guesses() -> None:
    # Regression test: an early version trusted the LLM's own title/venue/
    # start-time output, which occasionally merged two lines into one garbled
    # title and fell back to a placeholder venue ("null", "No venue
    # specified") instead of the venue already known for certain from the API.
    from datetime import timedelta

    garbled = ExtractedEvent(
        title="Les 7 Parnassiens Rita et Crocodile",
        event_type=EventType.AVANT_PREMIERE,
        venue="No venue specified",
        starts_at=datetime.now(UTC) + timedelta(days=3),  # plausible, but wrong
        has_team_present=True,
    )
    extractor = _extractor_returning(garbled)
    scraper = ParisCineInfoScraper(extractor, "user@example.test", "secret")
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(text="login_success"))
    client.get = AsyncMock(
        side_effect=[
            _FakeResponse(json_data={"data": [_movie(ti="Häxan")]}),
            _FakeResponse(
                json_data={
                    "showtimes": [
                        _showtime(title="Le Grand Action", start="2026-07-08T20:00:00")
                    ]
                }
            ),
        ]
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert len(sightings) == 1
    extracted = sightings[0].extracted
    assert extracted.title == "Häxan"
    assert extracted.venue == "Le Grand Action"
    assert extracted.starts_at == datetime(2026, 7, 8, 18, 0, tzinfo=UTC)
    # The LLM's classification of the comment is still kept.
    assert extracted.has_team_present is True


async def test_fetch_events_skips_films_with_no_commented_showtime() -> None:
    extractor = _extractor_returning(_sample_extracted())
    scraper = ParisCineInfoScraper(extractor, "user@example.test", "secret")
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(text="login_success"))
    client.get = AsyncMock(
        side_effect=[
            _FakeResponse(json_data={"data": [_movie()]}),
            _FakeResponse(json_data={"showtimes": [_showtime(com="")]}),
        ]
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert sightings == []
