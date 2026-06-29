"""Tests for the Rich progress reporter and console helpers."""

from cine_event_bot.io.console import (
    RichReporter,
    build_progress,
    print_banner,
    print_ingestion_summary,
)
from cine_event_bot.pipeline import IngestionReport


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
    print_ingestion_summary(IngestionReport(events_ingested=3, sources_failed=1))
    print_banner("test banner")
