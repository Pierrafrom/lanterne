"""Tests for the Forum des images scraper (classic HTML + LLM)."""

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from cine_event_bot.core.models import EventType, ExtractedEvent, Sighting, Source
from cine_event_bot.core.progress import NullReporter
from cine_event_bot.io.scrapers.forumdesimages import ForumDesImagesScraper

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
        starts_at=datetime(2026, 7, 9, 17, 30, tzinfo=UTC),
    )


def test_parse_listings_gathers_one_listing_per_card() -> None:
    scraper = ForumDesImagesScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("forumdesimages_agenda.html"), reference_date=_REFERENCE
    )

    assert len(listings) == 2
    assert all(item.source is Source.FORUM_DES_IMAGES for item in listings)
    assert listings[0].source_url == (
        "https://www.forumdesimages.fr/avant-premiere-positif-soudain-de-ryusuke-hamaguchi"
    )


def test_parse_listings_embeds_reference_date_and_card_text() -> None:
    scraper = ForumDesImagesScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(
        _fixture("forumdesimages_agenda.html"), reference_date=_REFERENCE
    )
    text = listings[0].raw_text

    assert "Reference date: 2026-06-25" in text
    assert "Le Forum des images" in text
    assert "Avant-premières Positif saison 2025-2026" in text
    assert "Soudain de Ryûsuke Hamaguchi" in text
    assert "Mardi 7 juillet à 20h" in text
    assert "Ryûsuke Hamaguchi" in text


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
