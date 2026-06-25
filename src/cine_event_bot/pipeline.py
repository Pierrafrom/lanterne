"""Ingestion pipeline: scrape every source and persist deduplicated events.

Ties the four building blocks together — scrapers (which already structure
their source into :class:`ScreeningEvent`, via the LLM or direct mapping) and
the deduplicating repository. The pipeline itself stays agnostic to how each
source produces its events; it only iterates scrapers and upserts the result.

A failing source is logged and skipped so one broken scraper never aborts the
whole run.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from cine_event_bot.io.repository import EventRepository
from cine_event_bot.io.scrapers.base import SourceScraper
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)


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
    ) -> None:
        """Bind the pipeline to its scrapers and persistence repository.

        Args:
            scrapers: The source scrapers to run, in order.
            repository: Repository performing the deduplicating upserts.
        """
        self._scrapers = scrapers
        self._repository = repository

    async def run(self, client: httpx.AsyncClient) -> IngestionReport:
        """Scrape every source and persist its events, returning a report.

        Args:
            client: Shared async HTTP client passed to each scraper.

        Returns:
            An :class:`IngestionReport` summarising the run.
        """
        ingested = 0
        failed = 0
        for scraper in self._scrapers:
            count = await self._ingest_source(scraper, client)
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
        self, scraper: SourceScraper, client: httpx.AsyncClient
    ) -> int | None:
        """Ingest one source; return its event count, or None on failure."""
        try:
            events = await scraper.fetch_events(client)
        except Exception:
            logger.exception(
                "source scrape failed",
                extra={"ctx": {"source": scraper.source.value}},
            )
            return None
        for event in events:
            await self._repository.upsert(event)
        logger.info(
            "source ingested",
            extra={"ctx": {"source": scraper.source.value, "events": len(events)}},
        )
        return len(events)
