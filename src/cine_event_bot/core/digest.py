"""Weekly digest formatting.

Turns a set of screenings into the plain-text message broadcast to subscribers.
Pure and side-effect free, so it is fully unit-tested without Telegram.

User-facing strings are in **French**: the bot targets a French-speaking
Parisian audience (see the project ``CLAUDE.md``). Times are shown in Paris
local time, since the events are stored in UTC.
"""

from collections.abc import Sequence
from datetime import datetime
from itertools import groupby
from zoneinfo import ZoneInfo

from cine_event_bot.core.models import ScreeningEvent

_PARIS = ZoneInfo("Europe/Paris")
_WEEKDAYS = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)
_MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
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
    local = event.starts_at.astimezone(_PARIS)
    return f"{_WEEKDAYS[local.weekday()]} {local.day} {_MONTHS[local.month - 1]}"


def _event_line(event: ScreeningEvent) -> str:
    """Format one screening as a digest bullet line."""
    title = event.title
    if event.release_year:
        title = f"{title} ({event.release_year})"
    suffix = " ⭐ en présence de l'équipe" if event.has_team_present else ""
    return f"• {_time_label(event.starts_at)} — {title} · {event.venue}{suffix}"


def _time_label(moment: datetime) -> str:
    """Return the Paris-local time as ``20h`` or ``20h30``."""
    local = moment.astimezone(_PARIS)
    if local.minute:
        return f"{local.hour}h{local.minute:02d}"
    return f"{local.hour}h"
