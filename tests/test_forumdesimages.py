"""Tests for the Forum des images scraper (classic HTML + LLM)."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from lanterne.core.models import EventType, ExtractedEvent, Sighting, Source
from lanterne.core.progress import NullReporter
from lanterne.io.scrapers.forumdesimages import (
    ForumDesImagesScraper,
    _resolve_known_starts_at,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_REFERENCE = date(2026, 6, 25)


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _extractor_returning(event: ExtractedEvent) -> MagicMock:
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=event)
    return extractor


def _sample_extracted() -> ExtractedEvent:
    return ExtractedEvent(
        title="Mulholland Drive",
        event_type=EventType.RETROSPECTIVE,
        venue="Le Forum des images",
        # Relative to now: structure_via_llm rejects implausible dates.
        starts_at=datetime.now(UTC) + timedelta(days=14),
    )


def test_parse_listings_gathers_one_listing_per_card() -> None:
    scraper = ForumDesImagesScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("forumdesimages_agenda.html"), reference_date=_REFERENCE
    )

    assert len(listings) == 2
    assert all(item.listing.source is Source.FORUM_DES_IMAGES for item in listings)
    assert listings[0].listing.source_url == (
        "https://www.forumdesimages.fr/avant-premiere-positif-soudain-de-ryusuke-hamaguchi"
    )


def test_parse_listings_embeds_reference_date_and_card_text() -> None:
    scraper = ForumDesImagesScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("forumdesimages_agenda.html"), reference_date=_REFERENCE
    )
    text = listings[0].listing.raw_text

    assert "Reference date: 2026-06-25" in text
    assert "Le Forum des images" in text
    assert "Avant-premières Positif saison 2025-2026" in text
    assert "Soudain de Ryûsuke Hamaguchi" in text
    assert "Mardi 7 juillet à 20h" in text
    assert "Ryûsuke Hamaguchi" in text


def test_parse_listings_resolves_the_known_starts_at_deterministically() -> None:
    # "Mardi 7 juillet à 20h" resolved against 2026-06-25 -> 2026-07-07 20:00
    # Paris local (summer, UTC+2) -> 18:00 UTC. Confirms the date is computed
    # in Python, not left to the LLM (the bug that broke the now-retired
    # lechampo.py, see ADR 0012).
    scraper = ForumDesImagesScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("forumdesimages_agenda.html"), reference_date=_REFERENCE
    )

    assert listings[0].known_starts_at == datetime(2026, 7, 7, 18, 0, tzinfo=UTC)


def test_resolve_known_starts_at_returns_none_on_unexpected_text() -> None:
    assert _resolve_known_starts_at("some unexpected format", _REFERENCE) is None


def test_resolve_known_starts_at_returns_none_on_an_invalid_day_month() -> None:
    assert _resolve_known_starts_at("Lundi 30 février à 20h", _REFERENCE) is None


async def test_fetch_events_known_starts_at_wins_over_a_wrong_llm_guess() -> None:
    # Regression case: the LLM resolves the year-less date to something
    # plausible-looking but wrong (or even implausible/in the past, exactly
    # what broke the now-retired lechampo.py) — the known, deterministically
    # resolved date must win regardless of what the LLM returns.
    wrong_guess = ExtractedEvent(
        title="Soudain de Ryûsuke Hamaguchi",
        event_type=EventType.AVANT_PREMIERE,
        venue="Le Forum des images",
        starts_at=datetime(2020, 1, 1, tzinfo=UTC),  # clearly wrong, in the past
    )
    extractor = _extractor_returning(wrong_guess)
    scraper = ForumDesImagesScraper(extractor)
    response = MagicMock()
    response.text = _fixture("forumdesimages_agenda.html")
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    events = await scraper.fetch_events(client, NullReporter())

    assert len(events) == 2
    assert all(
        event.extracted.starts_at != datetime(2020, 1, 1, tzinfo=UTC)
        for event in events
    )


async def test_fetch_events_structures_each_card() -> None:
    extractor = _extractor_returning(_sample_extracted())
    scraper = ForumDesImagesScraper(extractor)
    response = MagicMock()
    response.text = _fixture("forumdesimages_agenda.html")
    response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    events = await scraper.fetch_events(client, NullReporter())

    assert len(events) == 2
    assert all(isinstance(event, Sighting) for event in events)
    assert extractor.extract.await_count == 2
    client.get.assert_awaited_once()
