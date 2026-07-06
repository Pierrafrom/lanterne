"""Shared builders for domain objects used across test modules."""

from datetime import UTC, datetime
from typing import Any

from cine_event_bot.core.models import (
    EventType,
    ExtractedEvent,
    Film,
    ScreeningEvent,
    Sighting,
    Source,
    Venue,
)

DEFAULT_START = datetime(2026, 7, 1, 20, 30, tzinfo=UTC)


def make_extracted(**overrides: Any) -> ExtractedEvent:
    fields: dict[str, Any] = {
        "title": "Dune",
        "event_type": EventType.AVANT_PREMIERE,
        "venue": "Le Grand Rex",
        "starts_at": DEFAULT_START,
    }
    fields.update(overrides)
    return ExtractedEvent(**fields)


def make_sighting(
    *,
    source: Source = Source.PREMIERE_PROJO,
    source_url: str | None = None,
    booking_url: str | None = None,
    **extracted_overrides: Any,
) -> Sighting:
    return Sighting(
        extracted=make_extracted(**extracted_overrides),
        source=source,
        source_url=source_url,
        booking_url=booking_url,
    )


def make_display_event(
    *,
    title: str = "Dune",
    venue: str = "Le Grand Rex",
    event_type: EventType = EventType.AVANT_PREMIERE,
    starts_at: datetime = DEFAULT_START,
    has_team_present: bool = False,
    release_year: int | None = None,
    overview: str | None = None,
) -> ScreeningEvent:
    """Build an in-memory event with film and venue attached (not persisted).

    For pure formatting tests (digest, Q&A answers) that never touch the
    database but read the ``film`` and ``venue`` relationships.
    """
    return ScreeningEvent(
        dedup_key=f"{title}|{venue}|{starts_at.isoformat()}",
        film=Film(
            title_key=title.lower(),
            title=title,
            release_year=release_year,
            overview=overview,
        ),
        venue=Venue(slug=venue.lower(), name=venue),
        event_type=event_type,
        starts_at=starts_at,
        has_team_present=has_team_present,
    )
