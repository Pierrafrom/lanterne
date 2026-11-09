"""Tests for the specialness classifier evaluation harness (pure logic, no I/O)."""

import json
from typing import Any

import pytest

from lanterne.core.models import VenueKind
from lanterne.core.specialness_evaluation import (
    GoldenCase,
    evaluate_cases,
    parse_golden_cases,
)

_GOLDEN_JSON = json.dumps(
    {
        "cases": [
            {
                "id": "team-present",
                "title": "Nouvelle Vague",
                "venue": "Le Grand Rex",
                "venue_kind": "independent",
                "has_team_present": True,
                "starts_at_year": 2026,
                "distinct_venue_count": 5,
                "weekly_showing_count": 5,
                "expected_special": True,
                "notes": "team present",
            }
        ]
    }
)


def _case(**overrides: Any) -> GoldenCase:
    fields: dict[str, Any] = {
        "case_id": "c",
        "title": "Film",
        "venue": "Le Grand Rex",
        "venue_kind": VenueKind.INDEPENDENT,
        "has_team_present": False,
        "cycle_name": None,
        "release_year": None,
        "starts_at_year": 2026,
        "distinct_venue_count": 10,
        "weekly_showing_count": 10,
        "expected_special": False,
        "notes": "",
    }
    fields.update(overrides)
    return GoldenCase(**fields)


def test_parse_golden_cases_reads_expected_fields() -> None:
    cases = parse_golden_cases(_GOLDEN_JSON)

    assert len(cases) == 1
    assert cases[0].case_id == "team-present"
    assert cases[0].venue_kind is VenueKind.INDEPENDENT
    assert cases[0].has_team_present is True
    assert cases[0].expected_special is True


def test_parse_golden_cases_rejects_invalid_payload() -> None:
    with pytest.raises(ValueError, match="cases"):
        parse_golden_cases("{}")


def test_evaluate_cases_scores_a_correct_positive() -> None:
    cases = [_case(case_id="a", has_team_present=True, expected_special=True)]

    summary = evaluate_cases(cases)

    assert summary.total_cases == 1
    assert summary.accuracy == 1.0
    assert summary.precision == 1.0
    assert summary.recall == 1.0
    assert summary.results[0].correct is True
    assert summary.results[0].reasons == ["team_present"]


def test_evaluate_cases_scores_a_correct_negative() -> None:
    cases = [_case(case_id="a", expected_special=False)]

    summary = evaluate_cases(cases)

    assert summary.accuracy == 1.0
    assert summary.results[0].actual_special is False
    assert summary.results[0].reasons is None


def test_evaluate_cases_flags_a_false_positive() -> None:
    # The rules fire (team present) but the golden label disagrees.
    cases = [_case(case_id="a", has_team_present=True, expected_special=False)]

    summary = evaluate_cases(cases)

    assert summary.accuracy == 0.0
    assert summary.precision == 0.0
    assert summary.results[0].correct is False


def test_evaluate_cases_flags_a_false_negative() -> None:
    # The golden label says special, but no rule fires on these facts.
    cases = [_case(case_id="a", expected_special=True)]

    summary = evaluate_cases(cases)

    assert summary.accuracy == 0.0
    assert summary.recall == 0.0
    assert summary.results[0].correct is False


def test_evaluate_cases_aggregates_across_mixed_cases() -> None:
    cases = [
        _case(case_id="tp", has_team_present=True, expected_special=True),
        _case(case_id="ok", expected_special=False),
        _case(case_id="fn", expected_special=True),  # no rule fires -> wrong
    ]

    summary = evaluate_cases(cases)

    assert summary.total_cases == 3
    assert summary.accuracy == pytest.approx(2 / 3)
    assert summary.precision == 1.0  # the one flagged case was correctly special
    assert summary.recall == 0.5  # only one of the two expected-special cases fired


def test_evaluate_cases_on_empty_dataset_returns_zero_scores() -> None:
    summary = evaluate_cases([])

    assert summary.total_cases == 0
    assert summary.accuracy == 0.0
    assert summary.precision == 0.0
    assert summary.recall == 0.0
