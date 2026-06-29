"""Rich console helpers: a live progress display for the scrape command.

Implements the pipeline's :class:`ProgressReporter` with a Rich progress bar —
one row per source, showing a spinner while scraping (the LLM step has no known
total) and a filling bar while enriching and persisting. Uses the shared console
from :mod:`cine_event_bot.logging_config` so log lines and bars coexist.
"""

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from cine_event_bot.io.repository import EventStats
from cine_event_bot.logging_config import console
from cine_event_bot.pipeline import IngestionReport


def build_progress() -> Progress:
    """Build the progress display used during a scrape run."""
    return Progress(
        SpinnerColumn(spinner_name="line"),
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )


class RichReporter:
    """Renders ingestion progress on a Rich :class:`Progress` display."""

    def __init__(self, progress: Progress) -> None:
        """Bind the reporter to a live progress display.

        Args:
            progress: An entered Rich ``Progress`` instance.
        """
        self._progress = progress
        self._tasks: dict[str, TaskID] = {}

    def source_started(self, source: str) -> None:
        """Add an indeterminate task while the source is being scraped."""
        self._tasks[source] = self._progress.add_task(f"{source}: scraping", total=None)

    def events_fetched(self, source: str, total: int) -> None:
        """Switch the source's task to a determinate bar over ``total`` events."""
        self._progress.update(
            self._tasks[source], total=total, description=f"{source}: enriching"
        )

    def event_processed(self, source: str) -> None:
        """Advance the source's bar by one persisted event."""
        self._progress.advance(self._tasks[source])

    def source_finished(self, source: str, count: int) -> None:
        """Mark the source done with its final event count."""
        self._progress.update(
            self._tasks[source], description=f"{source}: done ({count})"
        )

    def source_failed(self, source: str) -> None:
        """Mark the source as failed."""
        task_id = self._tasks.get(source)
        if task_id is not None:
            self._progress.update(
                task_id, description=f"{source}: FAILED", total=1, completed=1
            )


def print_ingestion_summary(report: IngestionReport) -> None:
    """Print a one-line summary of a finished ingestion run."""
    console.print(
        f"Ingested {report.events_ingested} event(s); "
        f"{report.sources_failed} source(s) failed."
    )


def print_banner(message: str) -> None:
    """Print a highlighted startup banner line."""
    console.rule(message)


def print_stats(stats: EventStats) -> None:
    """Print a summary of the stored events as a Rich table."""
    if stats.total == 0:
        console.print("No events stored yet. Run 'scrape' first.")
        return
    table = Table(title="Stored screenings")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Total events", str(stats.total))
    table.add_row("By source", _as_pairs(stats.by_source))
    table.add_row("By type", _as_pairs(stats.by_type))
    table.add_row("Team present", str(stats.team_present))
    table.add_row("TMDB enriched", str(stats.enriched))
    if stats.first_starts_at and stats.last_starts_at:
        span = f"{stats.first_starts_at:%Y-%m-%d} -> {stats.last_starts_at:%Y-%m-%d}"
        table.add_row("Date range", span)
    console.print(table)


def _as_pairs(counts: dict[str, int]) -> str:
    """Render a count mapping as ``key=value`` lines."""
    return "\n".join(f"{key}={value}" for key, value in counts.items())
