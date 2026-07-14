"""Stored-events summary snapshot — a pure value object, no I/O.

Computed by :meth:`~cine_event_bot.io.repository.EventRepository.stats`, and
consumed both by the ``stats`` CLI command (``io/console.py::print_stats``)
and the post-scrape admin report (``core/report.py::build_admin_report``) —
kept in ``core/`` rather than ``io/repository.py`` so the latter, a pure
formatting module, does not have to depend on the I/O layer to read it (see
``docs/architecture.md``'s ``core``/``io`` split).
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class EventStats:
    """A snapshot of the stored events, for the ``stats`` command.

    Attributes:
        total: Number of events stored.
        by_source: Sighting count per source domain — an event reported by two
            sources contributes to both counts.
        by_type: Event count per event type.
        by_venue_kind: Event count per :class:`~cine_event_bot.core.models.VenueKind`
            — a drift signal: a healthy run keeps roughly the same shape
            (mostly chain/independent screenings, a small institution
            share), so a sudden shift flags a broken source or classifier
            change before it shows up anywhere else.
        special_count: Number of events flagged ``is_special``.
        team_present: Number of events with the film team present.
        enriched: Number of events whose film has a TMDB match.
        first_starts_at: Earliest screening start, or None when empty.
        last_starts_at: Latest screening start, or None when empty.
    """

    total: int
    by_source: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    by_venue_kind: dict[str, int] = field(default_factory=dict)
    special_count: int = 0
    team_present: int = 0
    enriched: int = 0
    first_starts_at: datetime | None = None
    last_starts_at: datetime | None = None

    @property
    def specialization_rate(self) -> float:
        """Share of stored events flagged ``is_special``, or 0.0 when empty."""
        return self.special_count / self.total if self.total else 0.0
