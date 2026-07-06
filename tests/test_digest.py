"""Tests for the weekly digest formatting (pure, French user-facing text)."""

from datetime import UTC, datetime

from factories import make_display_event

from cine_event_bot.core.digest import build_digest


def test_build_digest_without_events_is_explicit() -> None:
    message = build_digest([])

    assert "Aucune séance" in message


def test_build_digest_lists_title_venue_and_paris_time() -> None:
    # 18:30 UTC in July = 20h30 Paris (UTC+2).
    event = make_display_event(
        title="Dune",
        starts_at=datetime(2026, 7, 7, 18, 30, tzinfo=UTC),
        venue="Le Grand Rex",
    )

    message = build_digest([event])

    assert "Dune" in message
    assert "Le Grand Rex" in message
    assert "20h30" in message
    assert "mardi 7 juillet" in message.lower()


def test_build_digest_flags_team_presence_and_release_year() -> None:
    event = make_display_event(
        title="Soudain",
        starts_at=datetime(2026, 7, 7, 18, 0, tzinfo=UTC),
        has_team_present=True,
        release_year=2024,
    )

    message = build_digest([event])

    assert "(2024)" in message
    assert "équipe" in message


def test_build_digest_groups_by_day_in_chronological_order() -> None:
    later = make_display_event(
        title="Later", starts_at=datetime(2026, 7, 9, 17, 0, tzinfo=UTC)
    )
    earlier = make_display_event(
        title="Earlier", starts_at=datetime(2026, 7, 7, 17, 0, tzinfo=UTC)
    )

    message = build_digest([later, earlier])

    assert message.index("Earlier") < message.index("Later")
    assert message.index("7 juillet") < message.index("9 juillet")


def test_build_digest_handles_round_hour_without_minutes() -> None:
    event = make_display_event(
        title="Film", starts_at=datetime(2026, 7, 7, 18, 0, tzinfo=UTC)
    )

    message = build_digest([event])

    assert "20h" in message
    assert "20h00" not in message


def test_build_digest_treats_naive_datetime_as_utc() -> None:
    # SQLite hands datetimes back without tzinfo; they must be read as UTC.
    naive = datetime(2026, 7, 7, 18, 0)  # noqa: DTZ001 — simulating a SQLite read
    event = make_display_event(title="Film", starts_at=naive)

    message = build_digest([event])

    # 18:00 UTC -> 20h Paris (summer, UTC+2), not the server's local zone.
    assert "20h" in message
    assert "mardi 7 juillet" in message.lower()
