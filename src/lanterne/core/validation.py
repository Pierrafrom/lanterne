"""Plausibility guards for LLM-extracted screening data.

A local model occasionally hallucinates a date, drifts to the wrong year, or
returns an empty field that still satisfies the schema types. These checks
reject such extractions before they ever reach the database; scrapers log the
issues and drop the listing (see ``io/scrapers/base.py``).

Structured sources (direct JSON mapping, no LLM) are trusted and skip these
guards — their parsers already validate field by field.
"""

from datetime import UTC, datetime, timedelta

from lanterne.core.models import ExtractedEvent

# One day of slack in the past absorbs timezone wobble around midnight; a real
# programme never announces further ahead than roughly a season or two, so
# anything past ~18 months is a hallucinated date (usually a wrong year).
_MAX_PAST = timedelta(days=1)
_MAX_FUTURE = timedelta(days=550)

_MAX_TITLE_LENGTH = 200
_MAX_VENUE_LENGTH = 100


def find_extraction_issues(extracted: ExtractedEvent, *, now: datetime) -> list[str]:
    """Check an extraction for implausible values.

    Args:
        extracted: The LLM-extracted screening to check.
        now: Reference instant the date window is anchored on.

    Returns:
        One human-readable issue per failed check; empty when plausible.
    """
    issues: list[str] = []
    issues.extend(_date_issues(extracted.starts_at, now))
    issues.extend(_text_issues("title", extracted.title, _MAX_TITLE_LENGTH))
    issues.extend(_text_issues("venue", extracted.venue, _MAX_VENUE_LENGTH))
    return issues


def _date_issues(starts_at: datetime, now: datetime) -> list[str]:
    """Check that a start time falls inside the plausible programme window."""
    aware = starts_at if starts_at.tzinfo is not None else starts_at.replace(tzinfo=UTC)
    if aware < now - _MAX_PAST:
        return [f"starts_at is in the past ({aware.isoformat()})"]
    if aware > now + _MAX_FUTURE:
        return [f"starts_at is implausibly far ahead ({aware.isoformat()})"]
    return []


def _text_issues(field: str, value: str, max_length: int) -> list[str]:
    """Check that a required text field is non-blank and reasonably sized."""
    if not value.strip():
        return [f"{field} is blank"]
    if len(value) > max_length:
        return [f"{field} exceeds {max_length} characters ({len(value)})"]
    return []
