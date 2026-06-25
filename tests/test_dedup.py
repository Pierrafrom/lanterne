"""Tests for the deterministic deduplication key."""

from datetime import UTC, datetime

from cine_event_bot.core.dedup import compute_dedup_key


def _moment() -> datetime:
    return datetime(2026, 7, 1, 20, 30, tzinfo=UTC)


def test_dedup_key_is_deterministic() -> None:
    moment = _moment()

    first = compute_dedup_key("Dune", "Le Grand Rex", moment)
    second = compute_dedup_key("Dune", "Le Grand Rex", moment)

    assert first == second


def test_dedup_key_ignores_case_and_surrounding_whitespace() -> None:
    moment = _moment()

    assert compute_dedup_key("  Dune  ", "Le Grand Rex", moment) == compute_dedup_key(
        "dune", "le grand rex", moment
    )


def test_dedup_key_differs_for_distinct_screenings() -> None:
    moment = _moment()

    same_film_other_venue = compute_dedup_key("Dune", "Forum des images", moment)
    same_film_same_venue = compute_dedup_key("Dune", "Le Grand Rex", moment)

    assert same_film_other_venue != same_film_same_venue
