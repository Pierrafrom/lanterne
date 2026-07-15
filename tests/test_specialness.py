"""Tests for rule-based specialness classification."""

from datetime import UTC, datetime
from typing import Any

from factories import make_display_event

from lanterne.core.models import ScreeningEvent, VenueKind
from lanterne.core.specialness import FilmContext, classify_specialness

_STARTS_AT = datetime(2026, 7, 20, 20, 0, tzinfo=UTC)
# Comfortably above both aggregate-rule thresholds, so tests targeting the
# four per-event rules stay isolated from the two aggregate ones.
_WIDE_CONTEXT = FilmContext(distinct_venue_count=10, weekly_showing_count=10)


def _ordinary(**overrides: Any) -> ScreeningEvent:
    fields: dict[str, Any] = {
        "event_type": None,
        "is_special": False,
        "starts_at": _STARTS_AT,
    }
    fields.update(overrides)
    return make_display_event(**fields)


def test_stays_ordinary_with_no_signal() -> None:
    verdict = classify_specialness(_ordinary(), _WIDE_CONTEXT)

    assert verdict.is_special is False
    assert verdict.reasons is None
    assert verdict.confidence is None


def test_team_presence_makes_it_special() -> None:
    verdict = classify_specialness(_ordinary(has_team_present=True), _WIDE_CONTEXT)

    assert verdict.is_special is True
    assert verdict.reasons == ["team_present"]
    assert verdict.confidence == 1.0


def test_a_cycle_name_makes_it_special() -> None:
    verdict = classify_specialness(
        _ordinary(cycle_name="Rétrospective Kurosawa"), _WIDE_CONTEXT
    )

    assert verdict.is_special is True
    assert verdict.reasons == ["cycle_name"]
    assert verdict.confidence == 1.0


def test_an_institution_venue_makes_it_special() -> None:
    verdict = classify_specialness(
        _ordinary(venue_kind=VenueKind.INSTITUTION), _WIDE_CONTEXT
    )

    assert verdict.is_special is True
    assert verdict.reasons == ["institution_venue"]
    assert verdict.confidence == 1.0


def test_an_old_film_makes_it_special_with_reduced_confidence() -> None:
    verdict = classify_specialness(
        _ordinary(release_year=2020), _WIDE_CONTEXT
    )  # 6 years old

    assert verdict.is_special is True
    assert verdict.reasons == ["repertory_rarity"]
    assert verdict.confidence == 0.7


def test_a_recent_film_below_the_age_threshold_stays_ordinary() -> None:
    verdict = classify_specialness(
        _ordinary(release_year=2025), _WIDE_CONTEXT
    )  # 1 year old

    assert verdict.is_special is False


def test_a_film_exactly_at_the_age_threshold_is_special() -> None:
    verdict = classify_specialness(
        _ordinary(release_year=2023), _WIDE_CONTEXT
    )  # exactly 3 years

    assert verdict.is_special is True
    assert verdict.reasons == ["repertory_rarity"]


def test_an_unenriched_film_with_no_release_year_stays_ordinary() -> None:
    verdict = classify_specialness(_ordinary(release_year=None), _WIDE_CONTEXT)

    assert verdict.is_special is False


def test_multiple_fired_rules_combine_reasons_and_take_the_max_confidence() -> None:
    verdict = classify_specialness(
        _ordinary(has_team_present=True, release_year=2020), _WIDE_CONTEXT
    )

    assert verdict.is_special is True
    assert verdict.reasons == ["repertory_rarity", "team_present"]
    assert verdict.confidence == 1.0


def test_a_film_at_few_venues_makes_it_special() -> None:
    context = FilmContext(distinct_venue_count=3, weekly_showing_count=10)

    verdict = classify_specialness(_ordinary(), context)

    assert verdict.is_special is True
    assert verdict.reasons == ["rare_venue_count"]
    assert verdict.confidence == 0.6


def test_a_film_at_many_venues_stays_ordinary() -> None:
    context = FilmContext(distinct_venue_count=4, weekly_showing_count=10)

    verdict = classify_specialness(_ordinary(), context)

    assert verdict.is_special is False


def test_a_sparse_showing_count_makes_it_special() -> None:
    context = FilmContext(distinct_venue_count=10, weekly_showing_count=2)

    verdict = classify_specialness(_ordinary(), context)

    assert verdict.is_special is True
    assert verdict.reasons == ["sparse_showing_frequency"]
    assert verdict.confidence == 0.5


def test_a_normal_showing_count_stays_ordinary() -> None:
    context = FilmContext(distinct_venue_count=10, weekly_showing_count=3)

    verdict = classify_specialness(_ordinary(), context)

    assert verdict.is_special is False


def test_two_aggregate_rules_firing_takes_the_higher_confidence() -> None:
    context = FilmContext(distinct_venue_count=1, weekly_showing_count=1)

    verdict = classify_specialness(_ordinary(), context)

    assert verdict.is_special is True
    assert verdict.reasons == ["rare_venue_count", "sparse_showing_frequency"]
    assert verdict.confidence == 0.6
