"""Tests for the Rich progress reporter and console helpers."""

from lanterne.core.evaluation import CaseResult, EvaluationSummary
from lanterne.core.report import IngestionReport, SourceOutcome
from lanterne.core.specialness_evaluation import (
    CaseResult as SpecialnessCaseResult,
)
from lanterne.core.specialness_evaluation import (
    EvaluationSummary as SpecialnessEvaluationSummary,
)
from lanterne.core.stats import EventStats
from lanterne.io.console import (
    RichReporter,
    build_progress,
    print_banner,
    print_evaluation,
    print_ingestion_summary,
    print_specialness_evaluation,
    print_stats,
)


def test_rich_reporter_drives_a_full_source_lifecycle() -> None:
    progress = build_progress()
    reporter = RichReporter(progress)

    reporter.source_started("cinematheque.fr")
    reporter.events_fetched("cinematheque.fr", 2)
    reporter.event_processed("cinematheque.fr")
    reporter.event_processed("cinematheque.fr")
    reporter.source_finished("cinematheque.fr", 2)

    assert len(progress.tasks) == 1
    assert progress.tasks[0].completed == 2


def test_rich_reporter_handles_failure() -> None:
    reporter = RichReporter(build_progress())

    reporter.source_started("forumdesimages.fr")
    reporter.source_failed("forumdesimages.fr")
    # Failing an unknown source is a no-op, not an error.
    reporter.source_failed("never-started")


def test_console_helpers_do_not_raise() -> None:
    print_ingestion_summary(
        IngestionReport(
            outcomes=(
                SourceOutcome(source="premiereprojo.fr", events=3),
                SourceOutcome(source="cinematheque.fr", events=None),
            )
        )
    )
    print_banner("test banner")


def test_print_stats_on_an_empty_database_does_not_raise() -> None:
    print_stats(EventStats(total=0))


def test_print_stats_renders_venue_kind_and_specialization_rate() -> None:
    print_stats(
        EventStats(
            total=4,
            by_venue_kind={"independent": 3, "institution": 1},
            special_count=1,
        )
    )


def test_print_evaluation_renders_mismatch_details() -> None:
    print_evaluation(
        EvaluationSummary(
            total_cases=2,
            failures=0,
            exact_match_rate=0.5,
            field_accuracy={"title": 1.0},
            mean_latency_seconds=0.1,
            results=[
                CaseResult(
                    case_id="case-1",
                    mismatches={"title": ("Expected Title", "Actual Title")},
                    failed=False,
                    latency_seconds=0.1,
                ),
            ],
        ),
        model="qwen2.5:7b",
    )


def test_print_specialness_evaluation_renders_misclassified_details() -> None:
    print_specialness_evaluation(
        SpecialnessEvaluationSummary(
            total_cases=2,
            accuracy=0.5,
            precision=0.5,
            recall=0.5,
            results=[
                SpecialnessCaseResult(
                    case_id="case-1",
                    expected_special=True,
                    actual_special=False,
                    reasons=None,
                ),
            ],
        )
    )
