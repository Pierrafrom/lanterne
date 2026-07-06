"""Tests for the LLM extraction evaluation harness (pure logic, fake LLM)."""

import json
from datetime import UTC, datetime

import pytest
from factories import make_extracted

from cine_event_bot.core.evaluation import (
    GoldenCase,
    compare_extraction,
    evaluate_cases,
    parse_golden_cases,
)
from cine_event_bot.core.models import ExtractedEvent

_GOLDEN_JSON = json.dumps(
    {
        "cases": [
            {
                "id": "avp-team",
                "raw_text": "Avant-première de Dune au Grand Rex...",
                "expected": {
                    "title": "Dune",
                    "event_type": "avant_premiere",
                    "venue": "Le Grand Rex",
                    "starts_at": "2026-07-01T20:30:00+00:00",
                    "has_team_present": True,
                },
            }
        ]
    }
)


class _FakeExtractor:
    def __init__(self, results: dict[str, ExtractedEvent | Exception]) -> None:
        self._results = results

    async def extract(self, raw_text: str) -> ExtractedEvent:
        result = self._results[raw_text]
        if isinstance(result, Exception):
            raise result
        return result


def _case(case_id: str, raw_text: str, expected: ExtractedEvent) -> GoldenCase:
    return GoldenCase(case_id=case_id, raw_text=raw_text, expected=expected)


def test_parse_golden_cases_reads_expected_extraction() -> None:
    cases = parse_golden_cases(_GOLDEN_JSON)

    assert len(cases) == 1
    assert cases[0].case_id == "avp-team"
    assert cases[0].expected.title == "Dune"
    assert cases[0].expected.has_team_present is True
    assert cases[0].expected.starts_at == datetime(2026, 7, 1, 20, 30, tzinfo=UTC)


def test_parse_golden_cases_rejects_invalid_payload() -> None:
    with pytest.raises(ValueError, match="cases"):
        parse_golden_cases("{}")


def test_compare_identical_extractions_has_no_mismatch() -> None:
    extracted = make_extracted()

    assert compare_extraction(extracted, extracted.model_copy()) == {}


def test_compare_is_lenient_on_casing_and_whitespace() -> None:
    expected = make_extracted(title="Dune", venue="Le Grand Rex")
    actual = make_extracted(title=" DUNE ", venue="le  grand rex")

    assert compare_extraction(expected, actual) == {}


def test_compare_reports_each_differing_field() -> None:
    expected = make_extracted(title="Dune", has_team_present=True)
    actual = make_extracted(title="Dune 2", has_team_present=False)

    mismatches = compare_extraction(expected, actual)

    assert set(mismatches) == {"title", "has_team_present"}


def test_compare_treats_naive_datetimes_as_utc() -> None:
    expected = make_extracted(starts_at=datetime(2026, 7, 1, 20, 30, tzinfo=UTC))
    actual = make_extracted(starts_at=datetime(2026, 7, 1, 20, 30))  # noqa: DTZ001

    assert compare_extraction(expected, actual) == {}


async def test_evaluate_cases_aggregates_field_accuracy() -> None:
    good = make_extracted()
    wrong_venue = make_extracted(venue="Autre salle")
    extractor = _FakeExtractor({"a": good, "b": wrong_venue})
    cases = [_case("a", "a", good), _case("b", "b", make_extracted())]

    summary = await evaluate_cases(extractor, cases)

    assert summary.total_cases == 2
    assert summary.failures == 0
    assert summary.exact_match_rate == 0.5
    assert summary.field_accuracy["venue"] == 0.5
    assert summary.field_accuracy["title"] == 1.0
    assert summary.mean_latency_seconds >= 0.0


async def test_evaluate_cases_counts_extraction_failures() -> None:
    extractor = _FakeExtractor({"a": ValueError("LLM down")})
    cases = [_case("a", "a", make_extracted())]

    summary = await evaluate_cases(extractor, cases)

    assert summary.failures == 1
    assert summary.exact_match_rate == 0.0
