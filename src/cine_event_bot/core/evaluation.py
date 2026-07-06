"""Evaluation harness for the LLM extraction quality.

Runs the extractor over a committed golden dataset (announcement texts paired
with the expected :class:`ExtractedEvent`) and scores the output per field, so
model or prompt changes can be compared objectively instead of eyeballed —
the prerequisite for adding more LLM-backed sources.

This module holds the pure pieces: the dataset format, the field comparison,
and the aggregation. The real LLM call and the console report live in the CLI
(``eval-extraction`` command).
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ValidationError

from cine_event_bot.core.dedup import normalize_text
from cine_event_bot.core.models import ExtractedEvent

_COMPARED_FIELDS = (
    "title",
    "event_type",
    "venue",
    "starts_at",
    "has_team_present",
    "cycle_name",
)


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One golden example: an announcement text and its expected extraction.

    Attributes:
        case_id: Short unique identifier, for the report.
        raw_text: The announcement text handed to the extractor, exactly as a
            scraper would produce it (including any reference-date line).
        expected: The extraction a perfect model would return.
    """

    case_id: str
    raw_text: str
    expected: ExtractedEvent


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Outcome of evaluating one golden case.

    Attributes:
        case_id: The evaluated case.
        mismatches: Per-field ``(expected, actual)`` repr pairs; empty on a
            perfect extraction.
        failed: Whether the extraction raised instead of returning.
        latency_seconds: Wall-clock duration of the extraction call.
    """

    case_id: str
    mismatches: dict[str, tuple[str, str]]
    failed: bool
    latency_seconds: float


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    """Aggregated scores of one evaluation run.

    Attributes:
        total_cases: Number of golden cases evaluated.
        failures: Cases where the extraction raised.
        exact_match_rate: Share of cases with every field correct.
        field_accuracy: Share of non-failed cases with that field correct.
        mean_latency_seconds: Mean extraction latency over all cases.
        results: The per-case details, in dataset order.
    """

    total_cases: int
    failures: int
    exact_match_rate: float
    field_accuracy: dict[str, float]
    mean_latency_seconds: float
    results: list[CaseResult]


class _Extractor(Protocol):
    """The slice of ``EventExtractor`` the evaluation needs."""

    async def extract(self, raw_text: str) -> ExtractedEvent:
        """Extract one screening from an announcement text."""
        ...


class _GoldenCasePayload(BaseModel):
    """JSON shape of one golden case."""

    id: str
    raw_text: str
    expected: ExtractedEvent


class _GoldenDatasetPayload(BaseModel):
    """JSON shape of the golden dataset file."""

    cases: list[_GoldenCasePayload]


def parse_golden_cases(json_text: str) -> list[GoldenCase]:
    """Parse the golden dataset from its JSON text.

    Args:
        json_text: Content of the golden dataset file.

    Returns:
        The golden cases, in file order.

    Raises:
        ValueError: If the JSON does not match the expected dataset shape.
    """
    try:
        payload = _GoldenDatasetPayload.model_validate(json.loads(json_text))
    except (json.JSONDecodeError, ValidationError) as error:
        raise ValueError(
            f"invalid golden dataset (expected 'cases'): {error}"
        ) from error
    return [
        GoldenCase(case_id=case.id, raw_text=case.raw_text, expected=case.expected)
        for case in payload.cases
    ]


def compare_extraction(
    expected: ExtractedEvent, actual: ExtractedEvent
) -> dict[str, tuple[str, str]]:
    """Compare an extraction to its golden expectation, field by field.

    Text fields are compared case- and whitespace-insensitively, datetimes as
    the same UTC instant (naive values read as UTC), other fields strictly.

    Args:
        expected: The golden extraction.
        actual: The extraction produced by the model.

    Returns:
        ``(expected, actual)`` display pairs keyed by differing field name;
        empty when the extraction is perfect.
    """
    mismatches: dict[str, tuple[str, str]] = {}
    for field in _COMPARED_FIELDS:
        expected_value = getattr(expected, field)
        actual_value = getattr(actual, field)
        if not _values_match(expected_value, actual_value):
            mismatches[field] = (repr(expected_value), repr(actual_value))
    return mismatches


async def evaluate_cases(
    extractor: _Extractor, cases: list[GoldenCase]
) -> EvaluationSummary:
    """Run the extractor over every golden case and aggregate the scores.

    Cases run sequentially: the target is a local Ollama instance and the
    per-case latency is itself a reported metric.

    Args:
        extractor: The LLM-backed extractor under evaluation.
        cases: The golden dataset.

    Returns:
        The aggregated :class:`EvaluationSummary`.
    """
    results = [await _evaluate_case(extractor, case) for case in cases]
    scored = [result for result in results if not result.failed]
    field_accuracy = {
        field: _field_accuracy(scored, field) for field in _COMPARED_FIELDS
    }
    return EvaluationSummary(
        total_cases=len(results),
        failures=sum(1 for result in results if result.failed),
        exact_match_rate=_share(
            results, lambda result: not result.failed and not result.mismatches
        ),
        field_accuracy=field_accuracy,
        mean_latency_seconds=(
            sum(result.latency_seconds for result in results) / len(results)
            if results
            else 0.0
        ),
        results=results,
    )


async def _evaluate_case(extractor: _Extractor, case: GoldenCase) -> CaseResult:
    """Evaluate one golden case, timing the extraction call."""
    started = time.perf_counter()
    try:
        actual = await extractor.extract(case.raw_text)
    except Exception as error:
        return CaseResult(
            case_id=case.case_id,
            mismatches={"<extraction>": ("a structured event", repr(error))},
            failed=True,
            latency_seconds=time.perf_counter() - started,
        )
    return CaseResult(
        case_id=case.case_id,
        mismatches=compare_extraction(case.expected, actual),
        failed=False,
        latency_seconds=time.perf_counter() - started,
    )


def _values_match(expected: object, actual: object) -> bool:
    """Compare two field values with type-appropriate leniency."""
    if isinstance(expected, datetime) and isinstance(actual, datetime):
        return _as_utc(expected) == _as_utc(actual)
    if isinstance(expected, str) and isinstance(actual, str):
        return normalize_text(expected) == normalize_text(actual)
    return expected == actual


def _as_utc(moment: datetime) -> datetime:
    """Return the instant as aware UTC, reading naive values as UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _field_accuracy(scored: list[CaseResult], field: str) -> float:
    """Return the share of scored results whose ``field`` matched."""
    return _share(scored, lambda result: field not in result.mismatches)


def _share(results: list[CaseResult], predicate: Callable[[CaseResult], bool]) -> float:
    """Return the fraction of results satisfying the predicate (0.0 if none)."""
    if not results:
        return 0.0
    return sum(1 for result in results if predicate(result)) / len(results)
