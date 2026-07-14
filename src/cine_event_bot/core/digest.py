"""Weekly digest formatting.

Turns a set of screenings into the HTML message broadcast to subscribers.
Pure and side-effect free, so it is fully unit-tested without Telegram.

User-facing strings are in **French**: the bot targets a French-speaking
Parisian audience (see the project ``CLAUDE.md``). Times are shown in Paris
local time via :mod:`cine_event_bot.core.frenchfmt`. The message is sent with
Telegram's HTML parse mode (see ``io/bot.py``), so every dynamic value (film
title, venue name) is HTML-escaped and each line gets a clickable "Réserver"
link when a booking URL is known.
"""

import html
from collections.abc import Sequence
from itertools import groupby

from cine_event_bot.core.frenchfmt import (
    booking_link_html,
    event_type_label,
    french_date,
    french_time,
)
from cine_event_bot.core.models import ScreeningEvent

_HEADER = "🎬 Séances spéciales de la semaine"
_EMPTY = "Aucune séance spéciale cette semaine."


def build_digest(events: Sequence[ScreeningEvent]) -> str:
    """Build the weekly digest message from the week's screenings.

    Events are shown in Paris local time, grouped by day in chronological order.

    Args:
        events: The screenings to include (any order).

    Returns:
        The formatted digest, or a short notice when there is no screening.
    """
    if not events:
        return _EMPTY
    ordered = sorted(events, key=lambda event: event.starts_at)
    blocks = [_HEADER]
    for day_label, day_events in groupby(ordered, key=_day_label):
        lines = [f"📅 {day_label}"]
        lines.extend(_event_line(event) for event in day_events)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _day_label(event: ScreeningEvent) -> str:
    """Return the French day label (e.g. ``mardi 7 juillet``) in Paris time."""
    return french_date(event.starts_at)


def _event_line(event: ScreeningEvent) -> str:
    """Format one screening as an HTML digest bullet line.

    Expects the event's ``film`` and ``venue`` relationships to be loaded
    (repository queries eager-load them).
    """
    title = event.film.title
    if event.film.release_year:
        title = f"{title} ({event.film.release_year})"
    label = event_type_label(event.event_type)
    suffix = " ⭐ en présence de l'équipe" if event.has_team_present else ""
    return (
        f"• {french_time(event.starts_at)} — {html.escape(title)} · "
        f"{html.escape(event.venue.name)} · {html.escape(label)}{suffix}"
        f"{booking_link_html(event.booking_url)}"
    )
