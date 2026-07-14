"""Tests for the Paris Ciné Info scraper (authenticated JSON API)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cine_event_bot.core.models import EventType, ExtractedEvent, Sighting, Source
from cine_event_bot.core.progress import NullReporter
from cine_event_bot.io.scrapers.base import VenueDetail
from cine_event_bot.io.scrapers.paris_cine_info import (
    ParisCineInfoScraper,
    build_showtime_item,
    parse_venue_passes,
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
        "tid": "C0140",
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


class TestBuildShowtimeItem:
    def test_builds_an_llm_candidate_from_a_commented_showtime(self) -> None:
        item = build_showtime_item(_movie(), _showtime())

        assert item is not None
        assert not isinstance(item, Sighting)
        assert item.listing.source is Source.PARIS_CINE_INFO
        assert "Le Grand Action" in item.listing.raw_text
        assert "Häxan" in item.listing.raw_text
        assert "Avant-première en présence de la réalisatrice." in item.listing.raw_text

    def test_carries_the_known_true_facts_separately_from_the_llm_input(self) -> None:
        item = build_showtime_item(_movie(), _showtime())

        assert item is not None
        assert not isinstance(item, Sighting)
        assert item.known_title == "Häxan"
        assert item.known_venue == "Le Grand Action"

    def test_uses_the_booking_link_as_source_url_and_booking_url(self) -> None:
        item = build_showtime_item(_movie(), _showtime())

        assert item is not None
        assert not isinstance(item, Sighting)
        assert item.listing.source_url == "https://example.test/booking/123"
        assert item.listing.booking_url == "https://example.test/booking/123"

    def test_falls_back_to_the_site_url_without_a_booking_link(self) -> None:
        item = build_showtime_item(_movie(), _showtime(book=""))

        assert item is not None
        assert not isinstance(item, Sighting)
        assert item.listing.source_url == "https://paris-cine.info/"
        assert item.listing.booking_url is None

    def test_builds_an_ordinary_sighting_directly_without_a_comment(self) -> None:
        for com in ("", None, "   "):
            item = build_showtime_item(_movie(), _showtime(com=com))

            assert isinstance(item, Sighting)
            assert item.extracted.event_type is None
            assert item.extracted.title == "Häxan"
            assert item.extracted.venue == "Le Grand Action"
            assert item.source is Source.PARIS_CINE_INFO

    def test_returns_none_with_missing_essential_fields(self) -> None:
        assert build_showtime_item(_movie(ti=None), _showtime()) is None
        assert build_showtime_item(_movie(), _showtime(title=None)) is None
        assert build_showtime_item(_movie(), _showtime(start="not-a-date")) is None

    def test_converts_paris_local_time_to_utc(self) -> None:
        # 20:00 Paris local in July (summer, UTC+2) -> 18:00 UTC.
        item = build_showtime_item(_movie(), _showtime(start="2026-07-08T20:00:00"))

        assert item is not None
        assert not isinstance(item, Sighting)
        assert item.known_starts_at == datetime(2026, 7, 8, 18, 0, tzinfo=UTC)

    def test_ordinary_sighting_also_converts_paris_local_time_to_utc(self) -> None:
        item = build_showtime_item(
            _movie(), _showtime(com=None, start="2026-07-08T20:00:00")
        )

        assert isinstance(item, Sighting)
        assert item.extracted.starts_at == datetime(2026, 7, 8, 18, 0, tzinfo=UTC)

    def test_llm_candidate_carries_the_showtimes_tid_as_venue_external_id(
        self,
    ) -> None:
        item = build_showtime_item(_movie(), _showtime(tid="W7510"))

        assert item is not None
        assert not isinstance(item, Sighting)
        assert item.known_venue_external_id == "W7510"

    def test_ordinary_sighting_carries_the_showtimes_tid_as_venue_external_id(
        self,
    ) -> None:
        item = build_showtime_item(_movie(), _showtime(com=None, tid="W7510"))

        assert isinstance(item, Sighting)
        assert item.venue_external_id == "W7510"

    def test_missing_tid_leaves_venue_external_id_none(self) -> None:
        ordinary = build_showtime_item(_movie(), _showtime(com=None, tid=None))
        candidate = build_showtime_item(_movie(), _showtime(tid=None))

        assert isinstance(ordinary, Sighting)
        assert ordinary.venue_external_id is None
        assert candidate is not None
        assert not isinstance(candidate, Sighting)
        assert candidate.known_venue_external_id is None


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
            _FakeResponse(json_data={"showtimes": [_showtime()]}),
        ]
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert len(sightings) == 1
    assert isinstance(sightings[0], Sighting)
    assert sightings[0].source is Source.PARIS_CINE_INFO
    client.post.assert_awaited_once()
    assert client.post.call_args.kwargs["data"]["email"] == "user@example.test"
    assert client.post.call_args.kwargs["data"]["password"] == "secret"


async def test_fetch_events_fetches_movies_without_the_events_filter() -> None:
    extractor = _extractor_returning(_sample_extracted())
    scraper = ParisCineInfoScraper(extractor, "user@example.test", "secret")
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(text="login_success"))
    client.get = AsyncMock(
        side_effect=[
            _FakeResponse(json_data={"data": []}),
        ]
    )

    await scraper.fetch_events(client, NullReporter())

    movies_call = client.get.call_args_list[0]
    assert "params" not in movies_call.kwargs or "events" not in (
        movies_call.kwargs.get("params") or {}
    )


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
    # The known-true tid survives the LLM round-trip too, not just title/venue/time.
    assert sightings[0].venue_external_id == "C0140"


async def test_fetch_events_stores_uncommented_showtimes_as_ordinary() -> None:
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

    assert len(sightings) == 1
    assert sightings[0].extracted.event_type is None
    assert sightings[0].venue_external_id == "C0140"
    # No comment means no text to classify — the LLM is never called.
    extractor.extract.assert_not_awaited()


def _homepage_html(cine_options_json: str) -> str:
    return (
        "<html><head></head><body><script>"
        f"const cineOptions = {cine_options_json};\n"
        "const cardOptions = [];"
        "</script></body></html>"
    )


class TestParseVenuePasses:
    def test_maps_venue_name_to_its_accepted_card_codes(self) -> None:
        html = _homepage_html(
            '[{"label":"Paris Centre","value":"75123","options":['
            '{"label":"Jeu de Paume","value":246,'
            '"cards":[{"cardname":"cip","extra":0},{"cardname":"ugc","extra":0}]}'
            "]}]"
        )

        passes = parse_venue_passes(html)

        assert passes == {"Jeu de Paume": ["cip", "ugc"]}

    def test_skips_a_venue_with_no_accepted_card(self) -> None:
        html = _homepage_html(
            '[{"label":"Paris Centre","value":"75123","options":['
            '{"label":"Auditorium du Musée du Louvre","value":100,"cards":[]}'
            "]}]"
        )

        assert parse_venue_passes(html) == {}

    def test_flattens_every_department(self) -> None:
        html = _homepage_html(
            '[{"label":"Paris Centre","value":"75123","options":['
            '{"label":"Jeu de Paume","value":246,'
            '"cards":[{"cardname":"ugc","extra":0}]}]},'
            '{"label":"Hauts-de-Seine","value":"92","options":['
            '{"label":"Le Rex","value":300,'
            '"cards":[{"cardname":"pass","extra":0}]}]}]'
        )

        passes = parse_venue_passes(html)

        assert passes == {"Jeu de Paume": ["ugc"], "Le Rex": ["pass"]}

    def test_returns_empty_mapping_when_the_marker_is_absent(self) -> None:
        assert parse_venue_passes("<html>no catalogue here</html>") == {}

    def test_returns_empty_mapping_on_malformed_json(self) -> None:
        html = "const cineOptions = [{not valid json};"

        assert parse_venue_passes(html) == {}


async def test_fetch_venue_passes_logs_in_then_parses_the_homepage() -> None:
    scraper = ParisCineInfoScraper(
        _extractor_returning(_sample_extracted()), "user@example.test", "secret"
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(text="login_success"))
    client.get = AsyncMock(
        return_value=_FakeResponse(
            text=_homepage_html(
                '[{"label":"Paris Centre","value":"75123","options":['
                '{"label":"Jeu de Paume","value":246,'
                '"cards":[{"cardname":"ugc","extra":0}]}]}]'
            )
        )
    )

    passes = await scraper.fetch_venue_passes(client)

    assert passes == {"Jeu de Paume": ["ugc"]}
    client.post.assert_awaited_once()


async def test_fetch_venue_passes_does_not_log_in_again_after_fetch_events() -> None:
    # Regression test: confirmed live, a second login attempt on a client
    # already authenticated by fetch_events is rejected by the site — the
    # scraper must log in at most once per instance, whichever method runs
    # first.
    scraper = ParisCineInfoScraper(
        _extractor_returning(_sample_extracted()), "user@example.test", "secret"
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(text="login_success"))
    client.get = AsyncMock(
        side_effect=[
            _FakeResponse(json_data={"data": []}),  # fetch_events' movie list
            _FakeResponse(
                text=_homepage_html(
                    '[{"label":"Paris Centre","value":"75123","options":['
                    '{"label":"Jeu de Paume","value":246,'
                    '"cards":[{"cardname":"ugc","extra":0}]}]}]'
                )
            ),
        ]
    )

    await scraper.fetch_events(client, NullReporter())
    passes = await scraper.fetch_venue_passes(client)

    assert passes == {"Jeu de Paume": ["ugc"]}
    client.post.assert_awaited_once()


def _theatre_payload(**overrides: Any) -> dict[str, Any]:
    theater = {
        "name": "Le Louxor",
        "addr": "170 Boulevard de Magenta 75010 Paris 10e",
        "url": "https://www.cinemalouxor.fr/films/",
        "screen_name": "1 Youssef Chahine",
        "screen_id": "1153",
        "seatCount": "334",
        "screenWidth": "9",
        "screenHeight": "5",
    }
    theater.update(overrides)
    return {"theater": [theater], "cards": [{"cardname": "ugc", "extra": "0"}]}


async def test_fetch_venue_details_before_fetch_events_returns_empty() -> None:
    scraper = ParisCineInfoScraper(
        _extractor_returning(_sample_extracted()), "user@example.test", "secret"
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(text="login_success"))

    assert await scraper.fetch_venue_details(client) == {}


async def test_fetch_venue_details_maps_a_single_screen_venues_full_detail() -> None:
    scraper = ParisCineInfoScraper(
        _extractor_returning(_sample_extracted()), "user@example.test", "secret"
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(text="login_success"))
    client.get = AsyncMock(
        side_effect=[
            _FakeResponse(json_data={"data": [_movie()]}),
            _FakeResponse(
                json_data={
                    "showtimes": [_showtime(com="", tid="W7510", screen_id=15750)]
                }
            ),
            _FakeResponse(json_data=_theatre_payload()),
        ]
    )

    await scraper.fetch_events(client, NullReporter())
    details = await scraper.fetch_venue_details(client)

    assert details == {
        "Le Grand Action": VenueDetail(
            address="170 Boulevard de Magenta 75010 Paris 10e",
            website="https://www.cinemalouxor.fr/films/",
            seat_count=334,
            screen_width_m=9.0,
            screen_height_m=5.0,
        )
    }


async def test_fetch_venue_details_skips_room_fields_for_a_multi_screen_venue() -> None:
    # The same venue name reports two distinct (tid, screen_id) pairs this
    # run — ambiguous which screen's seat count/screen size would apply, so
    # those fields are left None while address/website (cinema-level, the
    # same regardless of room) are still mapped.
    scraper = ParisCineInfoScraper(
        _extractor_returning(_sample_extracted()), "user@example.test", "secret"
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(text="login_success"))
    client.get = AsyncMock(
        side_effect=[
            _FakeResponse(json_data={"data": [_movie()]}),
            _FakeResponse(
                json_data={
                    "showtimes": [
                        _showtime(
                            com="",
                            tid="W7510",
                            screen_id=15750,
                            start="2026-07-08T20:00:00",
                        ),
                        _showtime(
                            com="",
                            tid="W7510",
                            screen_id=15752,
                            start="2026-07-09T20:00:00",
                        ),
                    ]
                }
            ),
            _FakeResponse(json_data=_theatre_payload()),
        ]
    )

    await scraper.fetch_events(client, NullReporter())
    details = await scraper.fetch_venue_details(client)

    detail = details["Le Grand Action"]
    assert detail.address == "170 Boulevard de Magenta 75010 Paris 10e"
    assert detail.website == "https://www.cinemalouxor.fr/films/"
    assert detail.seat_count is None
    assert detail.screen_width_m is None
    assert detail.screen_height_m is None


async def test_fetch_venue_details_omits_a_venue_with_no_theater_data() -> None:
    scraper = ParisCineInfoScraper(
        _extractor_returning(_sample_extracted()), "user@example.test", "secret"
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(text="login_success"))
    client.get = AsyncMock(
        side_effect=[
            _FakeResponse(json_data={"data": [_movie()]}),
            _FakeResponse(
                json_data={
                    "showtimes": [_showtime(com="", tid="W7510", screen_id=15750)]
                }
            ),
            _FakeResponse(json_data={"theater": []}),
        ]
    )

    await scraper.fetch_events(client, NullReporter())
    details = await scraper.fetch_venue_details(client)

    assert details == {}
