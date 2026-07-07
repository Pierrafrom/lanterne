"""Tests for the ingestion report and its French admin summary."""

from cine_event_bot.core.report import (
    IngestionReport,
    SourceOutcome,
    build_admin_report,
)


def _report() -> IngestionReport:
    return IngestionReport(
        outcomes=(
            SourceOutcome(source="premiereprojo.fr", events=12),
            SourceOutcome(source="cinematheque.fr", events=None),
            SourceOutcome(source="forumdesimages.fr", events=0),
        )
    )


def test_report_aggregates_counts() -> None:
    report = _report()

    assert report.events_ingested == 12
    assert report.sources_failed == 1


def test_admin_report_flags_each_outcome_kind() -> None:
    message = build_admin_report(_report())

    assert "✅ premiereprojo.fr : 12 séance(s)" in message
    assert "❌ cinematheque.fr : échec du scrape" in message
    assert "⚠️ forumdesimages.fr : 0 séance" in message


def test_admin_report_warns_on_suspected_breakage() -> None:
    # A source returning zero events is the early signal its HTML changed.
    message = build_admin_report(_report())

    assert "source peut-être cassée" in message


def test_admin_report_all_good_has_no_warning() -> None:
    report = IngestionReport(
        outcomes=(SourceOutcome(source="premiereprojo.fr", events=3),)
    )

    message = build_admin_report(report)

    assert "✅" in message
    assert "❌" not in message
    assert "⚠️" not in message
