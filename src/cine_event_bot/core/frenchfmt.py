"""French date/time formatting for user-facing messages.

Shared by the weekly digest and the Q&A answers. Events are stored in UTC and
shown in Paris local time, with French weekday and month names (Python's
locale-based ``strftime`` is unreliable, so the names are tabled explicitly).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

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


def french_date(moment: datetime) -> str:
    """Return the Paris-local date in French, e.g. ``mardi 7 juillet``.

    Args:
        moment: A timezone-aware datetime.

    Returns:
        The weekday, day, and month in French (no year).
    """
    local = moment.astimezone(_PARIS)
    return f"{_WEEKDAYS[local.weekday()]} {local.day} {_MONTHS[local.month - 1]}"


def french_time(moment: datetime) -> str:
    """Return the Paris-local time as ``20h`` or ``20h30``.

    Args:
        moment: A timezone-aware datetime.

    Returns:
        The local time, omitting minutes when they are zero.
    """
    local = moment.astimezone(_PARIS)
    if local.minute:
        return f"{local.hour}h{local.minute:02d}"
    return f"{local.hour}h"
