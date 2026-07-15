"""Progress reporting protocol for long-running ingestion.

Lives in ``core`` so both the pipeline and the scrapers can depend on it without
a circular import. The scrapers report per-item progress *during* their slow LLM
work (so the bar is determinate), while the pipeline reports the per-source
lifecycle. A Rich-backed implementation lives in ``io/console.py``.
"""

from typing import Protocol


class ProgressReporter(Protocol):
    """Receives ingestion progress events for live display.

    All methods are best-effort UI hooks; implementations must not raise.
    """

    def source_started(self, source: str) -> None:
        """A source's scrape has begun."""
        ...

    def events_fetched(self, source: str, total: int) -> None:
        """The source has ``total`` items to process; switches the bar to it."""
        ...

    def event_processed(self, source: str) -> None:
        """One item of the current source has been processed."""
        ...

    def source_finished(self, source: str, count: int) -> None:
        """The source finished with ``count`` events ingested."""
        ...

    def source_failed(self, source: str) -> None:
        """The source's scrape raised and was skipped."""
        ...


class NullReporter:
    """A :class:`ProgressReporter` that ignores every event (default)."""

    def source_started(self, source: str) -> None:  # noqa: ARG002, D102
        return

    def events_fetched(self, source: str, total: int) -> None:  # noqa: ARG002, D102
        return

    def event_processed(self, source: str) -> None:  # noqa: ARG002, D102
        return

    def source_finished(self, source: str, count: int) -> None:  # noqa: ARG002, D102
        return

    def source_failed(self, source: str) -> None:  # noqa: ARG002, D102
        return
