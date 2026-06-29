"""Ingestion pipeline: scrape, enrich, and persist deduplicated events.

Ties the building blocks together — scrapers (which structure their source into
:class:`ScreeningEvent`, via the LLM or direct mapping), the TMDB enricher, and
the deduplicating repository. The pipeline stays agnostic to how each source
produces its events; it iterates scrapers, enriches each event, then upserts it.

A failing source is logged and skipped so one broken scraper never aborts the
whole run; a failing enrichment is logged and the event is still persisted.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from cine_event_bot.core.models import ScreeningEvent
from cine_event_bot.io.repository import EventRepository
from cine_event_bot.io.scrapers.base import SourceScraper
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)


class EventEnricher(Protocol):
    """Fills a screening's external metadata in place."""

    async def enrich(self, event: ScreeningEvent) -> None:
        """Enrich an event in place; a no-match leaves it untouched."""
        ...


class ProgressReporter(Protocol):
    """Receives ingestion progress events for live display.

    All methods are best-effort UI hooks; implementations must not raise.
    """

    def source_started(self, source: str) -> None:
        """A source's scrape has begun."""
        ...

    def events_fetched(self, source: str, total: int) -> None:
        """The source returned ``total`` events to enrich and persist."""
        ...

    def event_processed(self, source: str) -> None:
        """One event of the current source has been enriched and persisted."""
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


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """Outcome of one ingestion run.

    Attributes:
        events_ingested: Total events upserted across all sources.
        sources_failed: Number of sources whose scrape raised and was skipped.
    """

    events_ingested: int
    sources_failed: int


class IngestionPipeline:
    """Runs every scraper and upserts the events it yields."""

    def __init__(
        self,
        scrapers: Sequence[SourceScraper],
        repository: EventRepository,
        enricher: EventEnricher,
    ) -> None:
        """Bind the pipeline to its scrapers, enricher, and repository.

        Args:
            scrapers: The source scrapers to run, in order.
            repository: Repository performing the deduplicating upserts.
            enricher: Enricher filling each event's external metadata.
        """
        self._scrapers = scrapers
        self._repository = repository
        self._enricher = enricher

    async def run(
        self,
        client: httpx.AsyncClient,
        reporter: ProgressReporter | None = None,
    ) -> IngestionReport:
        """Scrape every source and persist its events, returning a report.

        Args:
            client: Shared async HTTP client passed to each scraper.
            reporter: Optional progress reporter for live display; defaults to a
                no-op reporter.

        Returns:
            An :class:`IngestionReport` summarising the run.
        """
        active = reporter if reporter is not None else NullReporter()
        ingested = 0
        failed = 0
        for scraper in self._scrapers:
            count = await self._ingest_source(scraper, client, active)
            if count is None:
                failed += 1
            else:
                ingested += count
        logger.info(
            "ingestion complete",
            extra={"ctx": {"events_ingested": ingested, "sources_failed": failed}},
        )
        return IngestionReport(events_ingested=ingested, sources_failed=failed)

    async def _ingest_source(
        self,
        scraper: SourceScraper,
        client: httpx.AsyncClient,
        reporter: ProgressReporter,
    ) -> int | None:
        """Ingest one source; return its event count, or None on failure."""
        source = scraper.source.value
        reporter.source_started(source)
        try:
            events = await scraper.fetch_events(client)
        except Exception:
            logger.exception("source scrape failed", extra={"ctx": {"source": source}})
            reporter.source_failed(source)
            return None
        reporter.events_fetched(source, len(events))
        for event in events:
            await self._enrich(event)
            await self._repository.upsert(event)
            reporter.event_processed(source)
        logger.info(
            "source ingested",
            extra={"ctx": {"source": source, "events": len(events)}},
        )
        reporter.source_finished(source, len(events))
        return len(events)

    async def _enrich(self, event: ScreeningEvent) -> None:
        """Enrich an event, logging and swallowing any enrichment failure."""
        try:
            await self._enricher.enrich(event)
        except Exception:
            logger.exception(
                "enrichment failed",
                extra={"ctx": {"title": event.title, "source": event.source.value}},
            )
