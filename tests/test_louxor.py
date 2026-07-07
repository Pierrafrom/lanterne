"""Tests for the Le Louxor scraper (per-event retrospective dossiers)."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from cine_event_bot.core.models import EventType, ExtractedEvent, Sighting, Source
from cine_event_bot.core.progress import NullReporter
from cine_event_bot.io.scrapers.louxor import LeLouxorScraper

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
        title="Conte d'été",
        event_type=EventType.RETROSPECTIVE,
        venue="Le Louxor",
        starts_at=datetime.now(UTC) + timedelta(days=1),
    )


def _response(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.raise_for_status = MagicMock()
    return response


def test_parse_index_extracts_deduplicated_event_urls_in_order() -> None:
    scraper = LeLouxorScraper(_extractor_returning(_sample_extracted()))

    urls = scraper.parse_index(_fixture("louxor_evenements.html"))

    assert urls == [
        "https://www.cinemalouxor.fr/events/69621-rohmer-et-lete-retrospective-en-6-films-du-1er-au-7-juillet/",
        "https://www.cinemalouxor.fr/events/75361-rohmer-lapres-midi/",
    ]


def test_parse_detail_splits_the_dossier_into_one_listing_per_film() -> None:
    scraper = LeLouxorScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_detail(
        _fixture("louxor_event_detail.html"),
        "https://www.cinemalouxor.fr/events/69621-x/",
        reference_date=_REFERENCE_DATE,
    )

    assert len(listings) == 2
    conte = next(listing for listing in listings if "CONTE D'ÉTÉ" in listing.raw_text)
    collectionneuse = next(
        listing for listing in listings if "LA COLLECTIONNEUSE" in listing.raw_text
    )
    # Each film's block must not bleed into the next one's.
    assert "LA COLLECTIONNEUSE" not in conte.raw_text
    assert "CONTE D'ÉTÉ" not in collectionneuse.raw_text


def test_parse_detail_gathers_venue_cycle_reference_date_and_pin_date() -> None:
    scraper = LeLouxorScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_detail(
        _fixture("louxor_event_detail.html"),
        "https://www.cinemalouxor.fr/events/69621-x/",
        reference_date=_REFERENCE_DATE,
    )

    conte = next(listing for listing in listings if "CONTE D'ÉTÉ" in listing.raw_text)
    assert "Le Louxor" in conte.raw_text
    assert "Reference date: 2026-06-20" in conte.raw_text
    assert "ROHMER ET L'ÉTÉ" in conte.raw_text
    assert "MERCREDI 1er JUILLET · 19H" in conte.raw_text
    assert conte.source is Source.LE_LOUXOR
    assert conte.source_url == "https://www.cinemalouxor.fr/events/69621-x/"


def test_parse_detail_ignores_the_page_banner_as_a_false_title() -> None:
    scraper = LeLouxorScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_detail(
        _fixture("louxor_event_detail.html"),
        "https://www.cinemalouxor.fr/events/69621-x/",
        reference_date=_REFERENCE_DATE,
    )

    # Only the two real films become listings — the "DU 1er AU 7 JUILLET" /
    # "RÉTROSPECTIVE EN 6 FILMS" banner lines must not be mistaken for titles.
    assert len(listings) == 2


async def test_fetch_events_structures_every_film_across_every_event() -> None:
    extractor = _extractor_returning(_sample_extracted())
    scraper = LeLouxorScraper(extractor)
    client = MagicMock()
    client.get = AsyncMock(
        side_effect=[
            _response(_fixture("louxor_evenements.html")),
            _response(_fixture("louxor_event_detail.html")),
            _response(_fixture("louxor_event_detail.html")),
        ]
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert len(sightings) == 4  # 2 films x 2 (deduplicated) event pages
    assert all(isinstance(sighting, Sighting) for sighting in sightings)
    assert all(sighting.source is Source.LE_LOUXOR for sighting in sightings)


async def test_fetch_events_skips_films_whose_extraction_fails() -> None:
    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=ValueError("LLM down"))
    scraper = LeLouxorScraper(extractor)
    client = MagicMock()
    client.get = AsyncMock(
        side_effect=[
            _response(_fixture("louxor_evenements.html")),
            _response(_fixture("louxor_event_detail.html")),
            _response(_fixture("louxor_event_detail.html")),
        ]
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert sightings == []
