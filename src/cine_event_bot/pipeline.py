"""Ingestion pipeline: scrape, persist deduplicated events, and enrich films.

Ties the building blocks together — scrapers (which structure their source into
:class:`Sighting` objects, via the LLM or direct mapping), the deduplicating
repository, and the TMDB film enricher. The pipeline stays agnostic to how each
source produces its sightings; it iterates scrapers, ingests each sighting,
then enriches the resulting film when it has no TMDB match yet.

A failing source is logged and skipped so one broken scraper never aborts the
whole run; a failing enrichment is logged and the event stays persisted.
"""

from collections.abc import Sequence
from typing import Protocol

import httpx

from cine_event_bot.core.models import Film, ScreeningEvent
from cine_event_bot.core.progress import NullReporter, ProgressReporter
from cine_event_bot.core.report import IngestionReport, SourceOutcome
from cine_event_bot.io.repository import EventRepository
from cine_event_bot.io.scrapers.base import SourceScraper
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)


class FilmEnricher(Protocol):
    """Fills a film's external metadata in place."""

    async def enrich(self, film: Film) -> None:
        """Enrich a film in place; a no-match leaves it untouched."""
        ...


class IngestionPipeline:
    """Runs every scraper and ingests the sightings it yields."""

    def __init__(
        self,
        scrapers: Sequence[SourceScraper],
        repository: EventRepository,
        enricher: FilmEnricher,
    ) -> None:
        """Bind the pipeline to its scrapers, enricher, and repository.

        Args:
            scrapers: The source scrapers to run, in order.
            repository: Repository performing the deduplicating ingestion.
            enricher: Enricher filling each film's external metadata.
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
        outcomes = tuple(
            [
                SourceOutcome(
                    source=scraper.source.value,
                    events=await self._ingest_source(scraper, client, active),
                )
                for scraper in self._scrapers
            ]
        )
        report = IngestionReport(outcomes=outcomes)
        logger.info(
            "ingestion complete",
            extra={
                "ctx": {
                    "events_ingested": report.events_ingested,
                    "sources_failed": report.sources_failed,
                }
            },
        )
        return report

    async def _ingest_source(
        self,
        scraper: SourceScraper,
        client: httpx.AsyncClient,
        reporter: ProgressReporter,
    ) -> int | None:
        """Ingest one source; return its sighting count, or None on failure."""
        source = scraper.source.value
        reporter.source_started(source)
        try:
            # The scraper reports its own per-item progress during the slow LLM
            # extraction, so the bar is determinate there rather than 0/?.
            sightings = await scraper.fetch_events(client, reporter)
        except Exception:
            logger.exception("source scrape failed", extra={"ctx": {"source": source}})
            reporter.source_failed(source)
            return None
        for sighting in sightings:
            event = await self._repository.ingest(sighting)
            await self._enrich(event)
        logger.info(
            "source ingested",
            extra={"ctx": {"source": source, "events": len(sightings)}},
        )
        reporter.source_finished(source, len(sightings))
        return len(sightings)

    async def _enrich(self, event: ScreeningEvent) -> None:
        """Enrich an event's film once, logging and swallowing any failure."""
        film = event.film
        if film.tmdb_id is not None:
            return
        try:
            await self._enricher.enrich(film)
            await self._repository.save_film(film)
        except Exception:
            logger.exception(
                "enrichment failed",
                extra={"ctx": {"title": film.title, "event_id": event.id}},
            )
