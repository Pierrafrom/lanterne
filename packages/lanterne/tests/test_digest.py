"""Tests for the weekly digest formatting (pure, French user-facing text)."""

from datetime import UTC, datetime

from factories import make_display_event

from lanterne.core.digest import build_digest
from lanterne.core.frenchfmt import event_type_label
from lanterne.core.models import EventType


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


def test_build_digest_shows_the_event_type_in_french() -> None:
    concert = make_display_event(
        title="Metropolis",
        event_type=EventType.CINE_CONCERT,
        starts_at=datetime(2026, 7, 7, 18, 0, tzinfo=UTC),
    )

    message = build_digest([concert])

    assert "Ciné-concert" in message


def test_build_digest_renders_a_clickable_booking_link_when_known() -> None:
    event = make_display_event(
        title="Dune",
        starts_at=datetime(2026, 7, 7, 18, 0, tzinfo=UTC),
        booking_url="https://example.com/tickets?id=42&seat=A1",
    )

    message = build_digest([event])

    assert '<a href="https://example.com/tickets?id=42&amp;seat=A1">Réserver</a>' in (
        message
    )


def test_build_digest_omits_booking_link_when_unknown() -> None:
    event = make_display_event(
        title="Dune",
        starts_at=datetime(2026, 7, 7, 18, 0, tzinfo=UTC),
        booking_url=None,
    )

    message = build_digest([event])

    assert "Réserver" not in message


def test_build_digest_escapes_html_special_characters_in_dynamic_text() -> None:
    event = make_display_event(
        title="Tom & Jerry",
        venue="<Le Grand Rex>",
        starts_at=datetime(2026, 7, 7, 18, 0, tzinfo=UTC),
    )

    message = build_digest([event])

    assert "Tom &amp; Jerry" in message
    assert "&lt;Le Grand Rex&gt;" in message
    assert "<Le Grand Rex>" not in message


def test_every_event_type_has_a_french_label() -> None:
    for member in EventType:
        assert event_type_label(member)


def test_ordinary_screening_gets_a_generic_label() -> None:
    assert event_type_label(None) == "Séance"
