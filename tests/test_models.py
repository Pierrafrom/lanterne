"""Tests for the domain models and the extraction-to-persistence factory."""

from datetime import UTC, datetime

from cine_event_bot.core.dedup import compute_dedup_key
from cine_event_bot.core.models import (
    EventType,
    ExtractedEvent,
    ScreeningEvent,
    Source,
)


def _extracted() -> ExtractedEvent:
    return ExtractedEvent(
        title="Dune: Part Two",
        event_type=EventType.AVANT_PREMIERE,
        venue="Le Grand Rex",
        starts_at=datetime(2026, 7, 1, 20, 30, tzinfo=UTC),
        has_team_present=True,
        description="Avant-première en présence du réalisateur.",
    )


def test_source_enum_carries_domain_url() -> None:
    assert Source.PREMIERE_PROJO.value == "premiereprojo.fr"
    assert Source.SORTIRAPARIS.value == "sortiraparis.com"


def test_extracted_event_requires_core_fields() -> None:
    extracted = _extracted()

    assert extracted.event_type is EventType.AVANT_PREMIERE
    assert extracted.has_team_present is True


def test_from_extracted_copies_fields_and_sets_dedup_key() -> None:
    extracted = _extracted()

    event = ScreeningEvent.from_extracted(
        extracted,
        source=Source.PREMIERE_PROJO,
        source_url="https://premiereprojo.fr/dune",
    )

    assert event.title == extracted.title
    assert event.event_type is EventType.AVANT_PREMIERE
    assert event.source is Source.PREMIERE_PROJO
    assert event.source_url == "https://premiereprojo.fr/dune"
    assert event.dedup_key == compute_dedup_key(
        extracted.title, extracted.venue, extracted.starts_at
    )


def test_from_extracted_leaves_tmdb_enrichment_empty() -> None:
    event = ScreeningEvent.from_extracted(
        _extracted(),
        source=Source.PREMIERE_PROJO,
        source_url=None,
    )

    assert event.tmdb_id is None
    assert event.overview is None
    assert event.poster_url is None
    assert event.release_year is None
