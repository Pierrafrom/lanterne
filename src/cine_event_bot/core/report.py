"""Ingestion run outcome and its admin-facing summary.

The pipeline records one :class:`SourceOutcome` per scraper; the aggregate
:class:`IngestionReport` feeds both the console summary and
:func:`build_admin_report`, the Telegram message that lets the operator spot a
broken source (an exception, or the zero-events signal of changed HTML)
without reading logs.

The admin message is in French, like every user-facing Telegram string (see
the project ``CLAUDE.md``).
"""

from dataclasses import dataclass

_HEADER = "🔎 Rapport de scrape"
_BREAKAGE_HINT = "(source peut-être cassée)"


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


def build_admin_report(report: IngestionReport) -> str:
    """Build the French admin summary of an ingestion run.

    One line per source: ✅ with the event count, ⚠️ when the source returned
    zero events (the early signal its HTML changed), ❌ when the scrape failed.

    Args:
        report: The finished run's report.

    Returns:
        The message to send to the admin chat.
    """
    lines = [_HEADER]
    lines.extend(_outcome_line(outcome) for outcome in report.outcomes)
    return "\n".join(lines)


def _outcome_line(outcome: SourceOutcome) -> str:
    """Format one source's outcome as a report line."""
    if outcome.events is None:
        return f"❌ {outcome.source} : échec du scrape (voir les logs)"
    if outcome.events == 0:
        return f"⚠️ {outcome.source} : 0 séance {_BREAKAGE_HINT}"
    return f"✅ {outcome.source} : {outcome.events} séance(s)"
