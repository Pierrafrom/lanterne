"""Tests for deterministic year-less French date resolution."""

from datetime import UTC, date, datetime

from cine_event_bot.core.frenchdate import resolve_next_occurrence, to_utc_datetime


def test_resolves_a_date_still_ahead_this_year() -> None:
    resolved = resolve_next_occurrence(22, "juillet", date(2026, 7, 6))

    assert resolved == date(2026, 7, 22)


def test_resolves_a_date_today_to_today() -> None:
    resolved = resolve_next_occurrence(6, "juillet", date(2026, 7, 6))

    assert resolved == date(2026, 7, 6)


def test_rolls_over_to_next_year_once_the_date_has_passed() -> None:
    # This is the exact failure mode confirmed live for the now-retired
    # lechampo.py: a day+month already earlier this year must resolve to
    # next year's occurrence, not to a date already in the past.
    resolved = resolve_next_occurrence(25, "juin", date(2026, 7, 14))

    assert resolved == date(2027, 6, 25)


def test_month_name_is_case_insensitive() -> None:
    resolved = resolve_next_occurrence(22, "Juillet", date(2026, 7, 6))

    assert resolved == date(2026, 7, 22)


def test_unknown_month_name_returns_none() -> None:
    assert resolve_next_occurrence(1, "not-a-month", date(2026, 7, 6)) is None


def test_invalid_day_for_the_month_returns_none() -> None:
    # 30 février does not exist in any year.
    assert resolve_next_occurrence(30, "février", date(2026, 1, 1)) is None


def test_to_utc_datetime_converts_paris_summer_time() -> None:
    # July is CEST (UTC+2): 20:00 Paris local -> 18:00 UTC.
    converted = to_utc_datetime(date(2026, 7, 22), 20, 0)

    assert converted == datetime(2026, 7, 22, 18, 0, tzinfo=UTC)


def test_to_utc_datetime_converts_paris_winter_time() -> None:
    # January is CET (UTC+1): 20:00 Paris local -> 19:00 UTC.
    converted = to_utc_datetime(date(2026, 1, 22), 20, 0)

    assert converted == datetime(2026, 1, 22, 19, 0, tzinfo=UTC)
