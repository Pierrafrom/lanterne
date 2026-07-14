"""Ingestion run outcome and its admin-facing summary.

The pipeline records one :class:`SourceOutcome` per scraper; the aggregate
:class:`IngestionReport` feeds both the console summary and
:func:`build_admin_report`, the Telegram message that lets the operator spot a
broken source (an exception, or the zero-events signal of changed HTML)
without reading logs. When called with the run's
:class:`~cine_event_bot.core.stats.EventStats` snapshot, the report also
appends a database-shape section (venue-kind breakdown, specialization rate)
— a second, slower-moving drift signal beyond the per-source event counts: a
healthy database keeps roughly the same shape run to run, so a sudden shift
flags a broken source or classifier change even when every source still
reported a plausible-looking count.

The admin message is in French, like every user-facing Telegram string (see
the project ``CLAUDE.md``).
"""

from dataclasses import dataclass

from cine_event_bot.core.models import Source
from cine_event_bot.core.stats import EventStats

_HEADER = "🔎 Rapport de scrape"
_BREAKAGE_HINT = "(source peut-être cassée)"
_DATABASE_HEADER = "📊 État de la base"

# Heuristic floors for the two width sources, from docs/coverage-matrix.md's
# one-time recon pass (15,491 Paris Ciné Info showtimes, 100+ offi.fr suburb
# venues) — generous enough to absorb normal week-to-week variance while
# still catching a near-total collapse (e.g. a login/selector break). Not
# applied to the low-volume bespoke sources: one of them (La Villette) is a
# genuinely seasonal programme where a real zero outside summer is expected,
# not a regression — see io/scrapers/lavillette.py. No historical-count
# storage backs this (YAGNI) — revisit once real run history exists.
_MIN_EXPECTED_EVENTS: dict[str, int] = {
    Source.PARIS_CINE_INFO.value: 1000,
    Source.OFFI.value: 200,
}


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """Outcome of scraping one source during an ingestion run.

    Attributes:
        source: The source's domain value.
        events: Number of sightings ingested, or ``None`` when the scrape
            raised and was skipped.
    """

    source: str
    events: int | None


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """Outcome of one ingestion run, one entry per source.

    Attributes:
        outcomes: Per-source outcomes, in scraper order.
    """

    outcomes: tuple[SourceOutcome, ...]

    @property
    def events_ingested(self) -> int:
        """Total sightings ingested across all sources."""
        return sum(
            outcome.events for outcome in self.outcomes if outcome.events is not None
        )

    @property
    def sources_failed(self) -> int:
        """Number of sources whose scrape raised and was skipped."""
        return sum(1 for outcome in self.outcomes if outcome.events is None)


def build_admin_report(report: IngestionReport, stats: EventStats | None = None) -> str:
    """Build the French admin summary of an ingestion run.

    One line per source: ✅ with the event count, ⚠️ when the source returned
    zero events (the early signal its HTML changed), ❌ when the scrape failed.

    Args:
        report: The finished run's report.
        stats: The database's post-run snapshot; when given, an "État de la
            base" section (venue-kind breakdown, specialization rate) is
            appended after the per-source lines. Omitted by default so
            callers with no cheap way to compute it (e.g. a test) are not
            forced to.

    Returns:
        The message to send to the admin chat.
    """
    lines = [_HEADER]
    lines.extend(_outcome_line(outcome) for outcome in report.outcomes)
    if stats is not None:
        lines.append("")
        lines.extend(_database_section(stats))
    return "\n".join(lines)


def _database_section(stats: EventStats) -> list[str]:
    """Build the "État de la base" section's lines."""
    lines = [_DATABASE_HEADER, f"Total : {stats.total} séance(s)"]
    if stats.by_venue_kind:
        by_kind = ", ".join(
            f"{kind}={count}" for kind, count in stats.by_venue_kind.items()
        )
        lines.append(f"Par type de salle : {by_kind}")
    lines.append(f"Taux de spécialisation : {stats.specialization_rate:.0%}")
    return lines


def _outcome_line(outcome: SourceOutcome) -> str:
    """Format one source's outcome as a report line."""
    if outcome.events is None:
        return f"❌ {outcome.source} : échec du scrape (voir les logs)"
    if outcome.events == 0:
        return f"⚠️ {outcome.source} : 0 séance {_BREAKAGE_HINT}"
    floor = _MIN_EXPECTED_EVENTS.get(outcome.source)
    if floor is not None and outcome.events < floor:
        return (
            f"⚠️ {outcome.source} : {outcome.events} séance(s), "
            f"volume anormalement bas (attendu ≥ {floor}) {_BREAKAGE_HINT}"
        )
    return f"✅ {outcome.source} : {outcome.events} séance(s)"
