"""French date/time formatting for user-facing messages.

Shared by the weekly digest and the Q&A answers. Events are stored in UTC and
shown in Paris local time, with French weekday and month names (Python's
locale-based ``strftime`` is unreliable, so the names are tabled explicitly).
Messages are sent with Telegram's HTML parse mode (see ``io/bot.py``), so
:func:`booking_link_html` is also shared here for the clickable "Réserver"
link appended to a screening line.

:data:`MONTHS_FR` is the output-direction (index → name) table; the parsing
counterpart (name → index, for resolving a year-less scraped date) is
``core/frenchdate.py``, which imports it from here to keep one source of
truth for French month names.
"""

import html
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from lanterne.core.models import EventType

_PARIS = ZoneInfo("Europe/Paris")
_BOOKING_LABEL = "Réserver"

_EVENT_TYPE_LABELS: dict[EventType, str] = {
    EventType.AVANT_PREMIERE: "Avant-première",
    EventType.CINE_CONCERT: "Ciné-concert",
    EventType.RETROSPECTIVE: "Rétrospective",
    EventType.OPEN_AIR: "Plein air",
    EventType.FESTIVAL: "Festival",
    EventType.SEANCE_CULTE: "Séance culte",
    EventType.CINE_CLUB: "Ciné-club",
    EventType.COURT_METRAGE: "Courts métrages",
}


_ORDINARY_SCREENING_LABEL = "Séance"


def event_type_label(event_type: EventType | None) -> str:
    """Return the French display label of a screening category.

    Args:
        event_type: The category to label, or ``None`` for an ordinary
            screening with no specific category.

    Returns:
        The label shown to users in the digest and Q&A answers.
    """
    if event_type is None:
        return _ORDINARY_SCREENING_LABEL
    return _EVENT_TYPE_LABELS[event_type]


def booking_link_html(booking_url: str | None) -> str:
    """Return a clickable "Réserver" HTML link suffix for a screening line.

    Args:
        booking_url: The screening's best available link (see
            ``EventRepository.ingest``), or ``None`` when no source ever
            reported one.

    Returns:
        A `` · <a href="...">Réserver</a>`` suffix ready to append to an
        HTML-parse-mode Telegram message line, or an empty string when there
        is no link to offer.
    """
    if booking_url is None:
        return ""
    return f' · <a href="{html.escape(booking_url, quote=True)}">{_BOOKING_LABEL}</a>'


def _to_paris(moment: datetime) -> datetime:
    """Convert any datetime to Paris local time.

    Events are stored as UTC, but SQLite hands them back timezone-naive; a naive
    value is therefore assumed to be UTC rather than the server's local zone.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(_PARIS)


_WEEKDAYS = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)
MONTHS_FR = (
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


def french_date(moment: datetime) -> str:
    """Return the Paris-local date in French, e.g. ``mardi 7 juillet``.

    Args:
        moment: A timezone-aware datetime.

    Returns:
        The weekday, day, and month in French (no year).
    """
    local = _to_paris(moment)
    return f"{_WEEKDAYS[local.weekday()]} {local.day} {MONTHS_FR[local.month - 1]}"


def french_time(moment: datetime) -> str:
    """Return the Paris-local time as ``20h`` or ``20h30``.

    Args:
        moment: A timezone-aware datetime.

    Returns:
        The local time, omitting minutes when they are zero.
    """
    local = _to_paris(moment)
    if local.minute:
        return f"{local.hour}h{local.minute:02d}"
    return f"{local.hour}h"
