"""Tests for the ingestion report and its French admin summary."""

from cine_event_bot.core.report import (
    IngestionReport,
    SourceOutcome,
    build_admin_report,
)
from cine_event_bot.core.stats import EventStats


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


def test_admin_report_without_stats_has_no_database_section() -> None:
    message = build_admin_report(_report())

    assert "État de la base" not in message


def test_admin_report_warns_on_a_suspiciously_low_width_source_volume() -> None:
    report = IngestionReport(
        outcomes=(SourceOutcome(source="paris-cine.info", events=5),)
    )

    message = build_admin_report(report)

    assert "⚠️ paris-cine.info : 5 séance(s)" in message
    assert "volume anormalement bas" in message
    assert "attendu ≥ 1000" in message


def test_admin_report_does_not_warn_on_a_healthy_width_source_volume() -> None:
    report = IngestionReport(
        outcomes=(SourceOutcome(source="paris-cine.info", events=12000),)
    )

    message = build_admin_report(report)

    assert "✅ paris-cine.info : 12000 séance(s)" in message
    assert "volume anormalement bas" not in message


def test_admin_report_does_not_apply_the_volume_floor_to_other_sources() -> None:
    # cinematheque.fr has no configured floor — a low but non-zero count from
    # a small bespoke source is normal, not a regression signal.
    report = IngestionReport(
        outcomes=(SourceOutcome(source="cinematheque.fr", events=2),)
    )

    message = build_admin_report(report)

    assert "✅ cinematheque.fr : 2 séance(s)" in message
    assert "volume anormalement bas" not in message


def test_admin_report_with_stats_shows_venue_kind_and_specialization_rate() -> None:
    stats = EventStats(
        total=200,
        by_venue_kind={"independent": 150, "institution": 50},
        special_count=20,
    )

    message = build_admin_report(_report(), stats)

    assert "État de la base" in message
    assert "200" in message
    assert "independent=150" in message
    assert "institution=50" in message
    assert "10%" in message
