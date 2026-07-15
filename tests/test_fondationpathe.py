"""Tests for the Fondation Jérôme Seydoux-Pathé scraper (agenda tile grid)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from lanterne.core.models import EventType, ExtractedEvent, Sighting, Source
from lanterne.core.progress import NullReporter
from lanterne.io.scrapers.fondationpathe import FondationPatheScraper

_FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _extractor_returning(event: ExtractedEvent) -> MagicMock:
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=event)
    return extractor


def _sample_extracted() -> ExtractedEvent:
    return ExtractedEvent(
        title="20,000 Leagues Under the Sea",
        event_type=EventType.RETROSPECTIVE,
        venue="Fondation Jérôme Seydoux-Pathé",
        starts_at=datetime.now(UTC) + timedelta(days=1),
    )


def _response(text: str) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.raise_for_status = MagicMock()
    return response


def test_parse_listings_keeps_only_seances_tagged_tiles() -> None:
    scraper = FondationPatheScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(_fixture("fondationpathe_agenda.html"))

    # 5 tiles in the fixture: EXPOSITIONS, CYCLES, SÉANCES, SÉANCES+combo,
    # ATELIERS — only the two SÉANCES-tagged ones are screenings.
    assert len(listings) == 2


def test_parse_listings_gathers_venue_date_and_title() -> None:
    scraper = FondationPatheScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(_fixture("fondationpathe_agenda.html"))

    submarine = next(
        listing for listing in listings if "20,000 Leagues" in listing.raw_text
    )
    assert submarine.source is Source.FONDATION_PATHE
    assert "Fondation Jérôme Seydoux-Pathé" in submarine.raw_text
    assert "07/07/2026 - 14:30" in submarine.raw_text
    assert (
        submarine.source_url
        == "https://www.fondation-jeromeseydoux-pathe.com/event/3266"
    )


def test_parse_listings_includes_combined_tags_containing_seances() -> None:
    scraper = FondationPatheScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(_fixture("fondationpathe_agenda.html"))

    assert any("Ciné-goûter en famille" in listing.raw_text for listing in listings)


def test_parse_listings_excludes_cycles_exhibitions_and_workshops() -> None:
    scraper = FondationPatheScraper(_extractor_returning(_sample_extracted()))

    listings = scraper.parse_listings(_fixture("fondationpathe_agenda.html"))

    raw_texts = [listing.raw_text for listing in listings]
    assert not any("Chantier invisible" in text for text in raw_texts)
    assert not any("Symbolisme et poésie" in text for text in raw_texts)
    assert not any("Tout en couleurs" in text for text in raw_texts)


async def test_fetch_events_structures_each_screening_tile() -> None:
    extractor = _extractor_returning(_sample_extracted())
    scraper = FondationPatheScraper(extractor)
    client = MagicMock()
    client.get = AsyncMock(
        return_value=_response(_fixture("fondationpathe_agenda.html"))
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert len(sightings) == 2
    assert all(isinstance(sighting, Sighting) for sighting in sightings)
    assert all(sighting.source is Source.FONDATION_PATHE for sighting in sightings)
    assert extractor.extract.await_count == 2


async def test_fetch_events_skips_tiles_whose_extraction_fails() -> None:
    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=ValueError("LLM down"))
    scraper = FondationPatheScraper(extractor)
    client = MagicMock()
    client.get = AsyncMock(
        return_value=_response(_fixture("fondationpathe_agenda.html"))
    )

    sightings = await scraper.fetch_events(client, NullReporter())

    assert sightings == []
