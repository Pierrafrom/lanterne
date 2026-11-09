"""Deterministic resolution of a year-less French day+month date.

Several Level 4 scrapers (``io/scrapers/forumdesimages.py``,
``io/scrapers/lavillette.py``) meet dates printed without a year (e.g.
"7 juillet") and must resolve them against the scrape's reference date.
Asking the LLM to do that arithmetic itself in free generation is
unreliable — confirmed live for the now-retired ``lechampo.py``, which
resolved both of its listings to a date already in the past on 2026-07-14
despite being given the reference date explicitly (see
``docs/decisions/0012-retire-lechampo.md``). :func:`resolve_next_occurrence`
does the resolution deterministically in Python instead, the same
"don't make the LLM do arithmetic it doesn't need to do" posture already
used by ``io/scrapers/offi.py`` for its own year-less day-tab dates.
"""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from lanterne.core.frenchfmt import MONTHS_FR

_MONTH_INDEX: dict[str, int] = {name: index + 1 for index, name in enumerate(MONTHS_FR)}

_PARIS = ZoneInfo("Europe/Paris")


def resolve_next_occurrence(
    day: int, month_name: str, reference_date: date
) -> date | None:
    """Return the next calendar date on/after ``reference_date`` matching day+month.

    Tries ``reference_date``'s year first; if that date has already passed,
    falls back to the same day/month next year — the "next upcoming
    occurrence" semantic a scraper's raw text implies but does not state.

    Args:
        day: Day of month as printed on the site (1-31, no year given).
        month_name: French month name, any casing (e.g. "Juillet", "juillet").
        reference_date: The date "next occurrence" is resolved relative to —
            typically the scrape's fetch date.

    Returns:
        The resolved date, or ``None`` when ``month_name`` is not a known
        French month or ``day`` is not a valid day for it in either
        candidate year (e.g. day 30 in February).
    """
    month = _MONTH_INDEX.get(month_name.lower())
    if month is None:
        return None
    for year in (reference_date.year, reference_date.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= reference_date:
            return candidate
    return None


def to_utc_datetime(resolved_date: date, hour: int, minute: int) -> datetime:
    """Combine a resolved date and a Paris-local time into a UTC datetime.

    French sites print screening times in local (Europe/Paris) time; every
    persisted ``starts_at`` is UTC (see ``core/models.py::ExtractedEvent``),
    so the conversion happens once here rather than being redone ad hoc per
    scraper — the same conversion ``io/scrapers/paris_cine_info.py``'s
    ``_parse_datetime`` already performs for its own source.

    Args:
        resolved_date: The screening's calendar date (see
            :func:`resolve_next_occurrence`).
        hour: Local hour (0-23), as printed on the site.
        minute: Local minute (0-59), as printed on the site.

    Returns:
        The equivalent UTC datetime.
    """
    local = datetime.combine(resolved_date, time(hour, minute), tzinfo=_PARIS)
    return local.astimezone(UTC)
