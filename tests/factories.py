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
    VenueKind,
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
    venue_external_id: str | None = None,
    **extracted_overrides: Any,
) -> Sighting:
    return Sighting(
        extracted=make_extracted(**extracted_overrides),
        source=source,
        source_url=source_url,
        booking_url=booking_url,
        venue_external_id=venue_external_id,
    )


def make_display_event(
    *,
    title: str = "Dune",
    venue: str = "Le Grand Rex",
    venue_kind: VenueKind = VenueKind.INDEPENDENT,
    event_type: EventType | None = EventType.AVANT_PREMIERE,
    is_special: bool = True,
    starts_at: datetime = DEFAULT_START,
    has_team_present: bool = False,
    cycle_name: str | None = None,
    release_year: int | None = None,
    overview: str | None = None,
    booking_url: str | None = None,
) -> ScreeningEvent:
    """Build an in-memory event with film and venue attached (not persisted).

    For pure formatting tests (digest, Q&A answers) and specialness
    classification tests that never touch the database but read the
    ``film``/``venue`` relationships. Defaults to a special screening; pass
    ``event_type=None, is_special=False`` for an ordinary screening.
    """
    return ScreeningEvent(
        dedup_key=f"{title}|{venue}|{starts_at.isoformat()}",
        film=Film(
            title_key=title.lower(),
            title=title,
            release_year=release_year,
            overview=overview,
        ),
        venue=Venue(slug=venue.lower(), name=venue, kind=venue_kind),
        event_type=event_type,
        is_special=is_special,
        starts_at=starts_at,
        has_team_present=has_team_present,
        cycle_name=cycle_name,
        booking_url=booking_url,
    )
