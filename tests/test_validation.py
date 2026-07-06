"""Tests for the post-extraction plausibility guards."""

from datetime import UTC, datetime, timedelta

from factories import make_extracted

from cine_event_bot.core.validation import find_extraction_issues

_NOW = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


def test_plausible_extraction_has_no_issue() -> None:
    extracted = make_extracted(starts_at=_NOW + timedelta(days=10))

    assert find_extraction_issues(extracted, now=_NOW) == []


def test_past_screening_is_rejected() -> None:
    extracted = make_extracted(starts_at=_NOW - timedelta(days=3))

    issues = find_extraction_issues(extracted, now=_NOW)

    assert any("starts_at" in issue for issue in issues)


def test_yesterday_is_still_tolerated() -> None:
    # Timezone wobble around midnight must not reject a same-day screening.
    extracted = make_extracted(starts_at=_NOW - timedelta(hours=20))

    assert find_extraction_issues(extracted, now=_NOW) == []


def test_far_future_screening_is_rejected() -> None:
    # A hallucinated year (e.g. 2030) lands far beyond any real programme.
    extracted = make_extracted(starts_at=_NOW + timedelta(days=600))

    issues = find_extraction_issues(extracted, now=_NOW)

    assert any("starts_at" in issue for issue in issues)


def test_blank_title_is_rejected() -> None:
    extracted = make_extracted(title="   ")

    issues = find_extraction_issues(extracted, now=_NOW)

    assert any("title" in issue for issue in issues)


def test_blank_venue_is_rejected() -> None:
    extracted = make_extracted(venue="")

    issues = find_extraction_issues(extracted, now=_NOW)

    assert any("venue" in issue for issue in issues)


def test_absurdly_long_fields_are_rejected() -> None:
    extracted = make_extracted(title="x" * 300, venue="y" * 200)

    issues = find_extraction_issues(extracted, now=_NOW)

    assert any("title" in issue for issue in issues)
    assert any("venue" in issue for issue in issues)


def test_naive_datetime_is_treated_as_utc() -> None:
    naive = (_NOW + timedelta(days=5)).replace(tzinfo=None)
    extracted = make_extracted(starts_at=naive)

    assert find_extraction_issues(extracted, now=_NOW) == []
