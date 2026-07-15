"""Tests for the deterministic deduplication key."""

from datetime import UTC, datetime

from lanterne.core.dedup import (
    canonicalize_venue_name,
    compute_dedup_key,
    normalize_text,
)


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


def test_dedup_key_ignores_apostrophe_variant() -> None:
    moment = _moment()

    curly = compute_dedup_key("L’Écologie des sentiments", "mk2 nation", moment)
    straight = compute_dedup_key("L'Écologie des sentiments", "mk2 nation", moment)

    assert curly == straight


def test_normalize_text_treats_apostrophe_variants_as_equal() -> None:
    assert normalize_text("L’écologie") == normalize_text("L'écologie")
    assert normalize_text("aujourd‘hui") == normalize_text("aujourd'hui")


def test_canonicalize_venue_name_maps_a_known_alias() -> None:
    assert canonicalize_venue_name("Forum des images") == "Le Forum des images"
    assert canonicalize_venue_name("forum DES IMAGES") == "Le Forum des images"


def test_canonicalize_venue_name_leaves_unknown_names_unchanged() -> None:
    assert canonicalize_venue_name("Le Grand Rex") == "Le Grand Rex"


def test_dedup_key_collapses_known_venue_aliases() -> None:
    moment = _moment()

    short_form = compute_dedup_key("Dune", "Forum des images", moment)
    long_form = compute_dedup_key("Dune", "Le Forum des images", moment)

    assert short_form == long_form
