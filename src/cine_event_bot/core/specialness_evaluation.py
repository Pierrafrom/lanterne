"""Evaluation harness for the rule-based specialness classifier.

Runs :func:`~cine_event_bot.core.specialness.classify_specialness` over a
committed golden dataset of labeled screenings and scores
accuracy/precision/recall, mirroring ``core/evaluation.py``'s pattern for the
LLM extractor — the objective signal ADR 0009 flagged as still missing before
the rule thresholds (3-year repertory-age cutoff, venue/showing-count
thresholds, confidence values) can be tuned from anything but reasoned
guesses.

Unlike the extraction harness, this one has no I/O to await: a golden case
carries the six rules' inputs directly — including the aggregate
``FilmContext`` fields (pre-computed venue/showing counts) a live database
would otherwise supply — so :func:`evaluate_cases` is a plain, synchronous,
dependency-free function. A case's ``starts_at_year`` stands in for a full
datetime: the only rule reading the screening's time
(``repertory_rarity``) compares calendar years, so nothing is lost by not
carrying a full ISO timestamp.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from cine_event_bot.core.models import Film, ScreeningEvent, Venue, VenueKind
from cine_event_bot.core.specialness import FilmContext, classify_specialness


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One golden example: a screening's facts and whether it should be special.

    Attributes:
        case_id: Short unique identifier, for the report.
        title: Film title (display only — the classifier does not read it).
        venue: Venue name (display only).
        venue_kind: Venue category the ``institution_venue`` rule reads.
        has_team_present: Whether the film team attends.
        cycle_name: Announced cycle/retrospective name, if any.
        release_year: The film's TMDB release year, if enriched.
        starts_at_year: Calendar year of the screening.
        distinct_venue_count: How many venues show this film.
        weekly_showing_count: How many showings this (film, venue) pair has.
        expected_special: Whether a correct classifier flags this special.
        notes: Free-text rationale for the label, for a human reviewing
            disagreements.
    """

    case_id: str
    title: str
    venue: str
    venue_kind: VenueKind
    has_team_present: bool
    cycle_name: str | None
    release_year: int | None
    starts_at_year: int
    distinct_venue_count: int
    weekly_showing_count: int
    expected_special: bool
    notes: str


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Outcome of evaluating one golden case.

    Attributes:
        case_id: The evaluated case.
        expected_special: The golden label.
        actual_special: What the classifier decided.
        reasons: Which rule(s) fired, or None when none did.
    """

    case_id: str
    expected_special: bool
    actual_special: bool
    reasons: list[str] | None

    @property
    def correct(self) -> bool:
        """Whether the classifier's verdict matched the golden label."""
        return self.expected_special == self.actual_special


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    """Aggregated scores of one evaluation run.

    Attributes:
        total_cases: Number of golden cases evaluated.
        accuracy: Share of cases where the verdict matched the label.
        precision: Of cases flagged special, the share that should have been
            (0.0 when nothing was flagged).
        recall: Of cases that should be special, the share flagged (0.0 when
            none should be).
        results: The per-case details, in dataset order.
    """

    total_cases: int
    accuracy: float
    precision: float
    recall: float
    results: list[CaseResult]


class _GoldenCasePayload(BaseModel):
    """JSON shape of one golden case."""

    id: str
    title: str
    venue: str
    venue_kind: VenueKind
    has_team_present: bool = False
    cycle_name: str | None = None
    release_year: int | None = None
    starts_at_year: int
    distinct_venue_count: int
    weekly_showing_count: int
    expected_special: bool
    notes: str = ""


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
        GoldenCase(
            case_id=case.id,
            title=case.title,
            venue=case.venue,
            venue_kind=case.venue_kind,
            has_team_present=case.has_team_present,
            cycle_name=case.cycle_name,
            release_year=case.release_year,
            starts_at_year=case.starts_at_year,
            distinct_venue_count=case.distinct_venue_count,
            weekly_showing_count=case.weekly_showing_count,
            expected_special=case.expected_special,
            notes=case.notes,
        )
        for case in payload.cases
    ]


def evaluate_cases(cases: list[GoldenCase]) -> EvaluationSummary:
    """Run the classifier over every golden case and aggregate the scores.

    Args:
        cases: The golden dataset.

    Returns:
        The aggregated :class:`EvaluationSummary`.
    """
    results = [_evaluate_case(case) for case in cases]
    return EvaluationSummary(
        total_cases=len(results),
        accuracy=_share(results, lambda result: result.correct),
        precision=_precision(results),
        recall=_recall(results),
        results=results,
    )


def _evaluate_case(case: GoldenCase) -> CaseResult:
    """Evaluate one golden case."""
    event = _build_event(case)
    context = FilmContext(
        distinct_venue_count=case.distinct_venue_count,
        weekly_showing_count=case.weekly_showing_count,
    )
    verdict = classify_specialness(event, context)
    return CaseResult(
        case_id=case.case_id,
        expected_special=case.expected_special,
        actual_special=verdict.is_special,
        reasons=verdict.reasons,
    )


def _build_event(case: GoldenCase) -> ScreeningEvent:
    """Build an in-memory event with film and venue attached, for classification."""
    return ScreeningEvent(
        dedup_key=f"{case.title}|{case.venue}|{case.starts_at_year}",
        film=Film(
            title_key=case.title.lower(),
            title=case.title,
            release_year=case.release_year,
        ),
        venue=Venue(slug=case.venue.lower(), name=case.venue, kind=case.venue_kind),
        starts_at=datetime(case.starts_at_year, 1, 1, tzinfo=UTC),
        has_team_present=case.has_team_present,
        cycle_name=case.cycle_name,
    )


def _precision(results: list[CaseResult]) -> float:
    """Share of classifier-flagged-special cases that should have been."""
    flagged = [result for result in results if result.actual_special]
    return _share(flagged, lambda result: result.expected_special)


def _recall(results: list[CaseResult]) -> float:
    """Share of golden-special cases the classifier flagged."""
    golden_special = [result for result in results if result.expected_special]
    return _share(golden_special, lambda result: result.actual_special)


def _share(results: list[CaseResult], predicate: Callable[[CaseResult], bool]) -> float:
    """Return the fraction of results satisfying the predicate (0.0 if none)."""
    if not results:
        return 0.0
    return sum(1 for result in results if predicate(result)) / len(results)
