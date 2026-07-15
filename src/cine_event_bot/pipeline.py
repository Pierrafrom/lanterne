"""Ingestion pipeline: scrape, persist deduplicated events, and enrich films.

Ties the building blocks together — scrapers (which structure their source into
:class:`Sighting` objects, via the LLM or direct mapping), the deduplicating
repository, the TMDB film enricher, and the rule-based specialness classifier.
The pipeline stays agnostic to how each source produces its sightings; every
source's fetch runs concurrently (:meth:`IngestionPipeline._fetch_source`),
then each source's sightings are ingested sequentially, in registration
order (:meth:`IngestionPipeline._ingest_sightings`), enriching the resulting
film when it has no TMDB match yet — see :meth:`IngestionPipeline.run`.

Once every scraper has run, a single end-of-run pass
(:meth:`IngestionPipeline._classify_pending_specialness`) evaluates every
still-ordinary screening currently stored — not just the ones touched this
run — against the rule-based classifier (``core/specialness.py``). This is
deliberately *not* inline per sighting: two of the six rules need a film's
final venue/showing counts for this run, and computing them mid-run would see
a partial, scraper-order-dependent count (e.g. the first venue ingested for a
film that turns out to be widely released would see a venue count of 1 and
wrongly qualify as "rare") that, once a verdict fires, is never revisited.

Three further end-of-run passes enrich already-stored venues and films,
each for any scraper that implements the relevant optional capability
(today only Paris Ciné Info for all three) — detected via ``isinstance``
against a ``runtime_checkable`` Protocol rather than special-casing one
source: :meth:`IngestionPipeline._apply_venue_passes` (accepted
subscription cards,
:class:`~cine_event_bot.io.scrapers.base.VenuePassSource`),
:meth:`IngestionPipeline._apply_venue_details` (address, official site,
room-level seat count/screen size,
:class:`~cine_event_bot.io.scrapers.base.VenueDetailSource`), and
:meth:`IngestionPipeline._apply_film_ratings` (IMDb/Allociné/SensCritique/
Rotten Tomatoes/Metacritic/Letterboxd ratings,
:class:`~cine_event_bot.io.scrapers.base.FilmRatingSource` — see
``docs/decisions/0013-film-ratings-from-paris-cine-info.md``).

Every classified screening's full feature vector and verdict is logged as
structured JSONL (``msg="specialness verdict"``, see
:func:`_log_specialness_verdict`) — both when a rule fires and when none
does, so ``grep '"msg":"specialness verdict"' logs/app.jsonl`` accumulates a
real labeled dataset from production runs, usable to retune the classifier's
thresholds instead of relying only on the small hand-built golden set (see
``docs/decisions/0009-specialness-rules-only.md``).

A failing source is logged and skipped so one broken scraper never aborts the
whole run; a failing enrichment is logged and the event stays persisted.
"""

import asyncio
from collections.abc import Sequence
from typing import Protocol

import httpx

from cine_event_bot.core.models import Film, ScreeningEvent, Sighting
from cine_event_bot.core.progress import NullReporter, ProgressReporter
from cine_event_bot.core.report import IngestionReport, SourceOutcome
from cine_event_bot.core.specialness import (
    FilmContext,
    SpecialnessVerdict,
    classify_specialness,
)
from cine_event_bot.io.repository import EventRepository
from cine_event_bot.io.scrapers.base import (
    FilmRatingSource,
    SourceScraper,
    VenueDetailSource,
    VenuePassSource,
)
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

        Fetching (network + LLM extraction) runs concurrently across every
        source — the slow, I/O-bound part gains nothing from being
        serialized. Ingestion (writing to the shared database session) then
        runs sequentially, in ``scrapers``' original order, preserving the
        first-source-wins dedup semantics :meth:`EventRepository.ingest
        <cine_event_bot.io.repository.EventRepository.ingest>` relies on —
        ``AsyncSession`` is not safe for concurrent writes, but DB writes are
        cheap relative to fetch/LLM time, so serializing only that half costs
        little (see ``docs/performance-audit.md``'s Finding 2).

        Args:
            client: Shared async HTTP client passed to each scraper.
            reporter: Optional progress reporter for live display; defaults to a
                no-op reporter.

        Returns:
            An :class:`IngestionReport` summarising the run.
        """
        active = reporter if reporter is not None else NullReporter()
        fetch_results = await asyncio.gather(
            *(self._fetch_source(scraper, client, active) for scraper in self._scrapers)
        )
        outcomes = tuple(
            [
                SourceOutcome(
                    source=scraper.source.value,
                    events=await self._ingest_sightings(
                        scraper.source.value, sightings, active
                    ),
                )
                for scraper, sightings in zip(
                    self._scrapers, fetch_results, strict=True
                )
            ]
        )
        await self._classify_pending_specialness()
        await self._apply_venue_passes(client)
        await self._apply_venue_details(client)
        await self._apply_film_ratings(client)
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

    async def _fetch_source(
        self,
        scraper: SourceScraper,
        client: httpx.AsyncClient,
        reporter: ProgressReporter,
    ) -> list[Sighting] | None:
        """Fetch one source's sightings; None on failure, logged and isolated.

        Run concurrently across every source (see :meth:`run`) — a failing
        or slow source never blocks another's fetch, matching the existing
        "one broken scraper never aborts the run" guarantee at the fetch
        level too.
        """
        source = scraper.source.value
        reporter.source_started(source)
        try:
            # The scraper reports its own per-item progress during the slow LLM
            # extraction, so the bar is determinate there rather than 0/?.
            return await scraper.fetch_events(client, reporter)
        except Exception:
            logger.exception("source scrape failed", extra={"ctx": {"source": source}})
            reporter.source_failed(source)
            return None

    async def _ingest_sightings(
        self,
        source: str,
        sightings: list[Sighting] | None,
        reporter: ProgressReporter,
    ) -> int | None:
        """Persist one source's already-fetched sightings.

        Args:
            source: The source value (for logging/reporting).
            sightings: The sightings :meth:`_fetch_source` returned, or
                ``None`` when that source's fetch failed.
            reporter: Progress reporter for live display.

        Returns:
            The number of sightings ingested, or ``None`` when ``sightings``
            was ``None``.
        """
        if sightings is None:
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
        """Enrich an event's film once, logging and swallowing any failure.

        ``title``/``event_id`` are captured before the attempt, not read from
        ``film``/``event`` inside the ``except`` block: an unexpected save
        failure makes ``EventRepository``'s commit roll back, which expires
        every object the session had loaded regardless of
        ``expire_on_commit`` — reading an expired attribute outside an
        ``await`` then raises ``MissingGreenlet`` instead of the log line it
        was meant to produce, turning one film's enrichment failure into a
        crash of the whole run. The expected failure mode — two
        differently-spelled titles matching the same TMDB film — is handled
        by :meth:`_save_or_merge` before it ever reaches a collision.
        """
        film = event.film
        if film.tmdb_id is not None:
            return
        title, event_id = film.title, event.id
        try:
            await self._enricher.enrich(film)
            await self._save_or_merge(film)
        except Exception:
            logger.exception(
                "enrichment failed",
                extra={"ctx": {"title": title, "event_id": event_id}},
            )

    async def _save_or_merge(self, film: Film) -> None:
        """Save a freshly-enriched film, merging into a duplicate owning its TMDB id.

        Two differently-worded announced titles for the same real film (a
        punctuation variant ``core/dedup.py::normalize_text`` does not
        unify) start as two different ``Film`` rows but can both resolve to
        the same TMDB match. Whichever reaches here second is retired into
        the first's row instead of colliding on ``Film.tmdb_id``'s unique
        constraint.
        """
        if film.tmdb_id is not None:
            existing = await self._repository.find_film_by_tmdb_id(film.tmdb_id)
            if existing is not None and existing.id != film.id:
                logger.info(
                    "merged duplicate film",
                    extra={
                        "ctx": {
                            "title": film.title,
                            "loser_film_id": film.id,
                            "winner_film_id": existing.id,
                        }
                    },
                )
                await self._repository.merge_film(loser=film, winner=existing)
                return
        await self._repository.save_film(film)

    async def _classify_pending_specialness(self) -> None:
        """Classify every still-ordinary screening once, after every scraper has run.

        Re-evaluates the full ``is_special=False`` set currently stored, not
        only the screenings touched this run — a screening left ordinary on
        an earlier run (e.g. its film had no TMDB match yet) is picked up
        again here once conditions change, without needing its own re-ingest.
        A screening already flagged special (e.g. by
        ``EventRepository.ingest``'s ``curated_source`` reason) never reaches
        this loop — the rules only ever upgrade, never downgrade or overwrite
        an existing verdict.

        The two aggregate counts every screening's :class:`FilmContext` needs
        are fetched once, for every film/venue at once, rather than
        per-screening — at real volume this is two queries instead of
        ``2 × len(events)`` (see ``docs/performance-audit.md``'s Finding 4).
        """
        events = await self._repository.list_ordinary_screenings()
        if not events:
            return
        venue_counts = await self._repository.venue_counts_by_film()
        screening_counts = await self._repository.screening_counts_by_film_and_venue()
        for event in events:
            context = FilmContext(
                distinct_venue_count=venue_counts.get(event.film_id, 0),
                weekly_showing_count=screening_counts.get(
                    (event.film_id, event.venue_id), 0
                ),
            )
            await self._classify_specialness(event, context)

    async def _classify_specialness(
        self, event: ScreeningEvent, context: FilmContext
    ) -> None:
        """Upgrade one ordinary screening's ``is_special`` flag when a rule fires."""
        verdict = classify_specialness(event, context)
        _log_specialness_verdict(event, context, verdict)
        if not verdict.is_special:
            return
        event.is_special = True
        event.specialness_reasons = verdict.reasons
        event.specialness_confidence = verdict.confidence
        await self._repository.save_event(event)

    async def _apply_venue_passes(self, client: httpx.AsyncClient) -> None:
        """Enrich stored venues from every scraper's optional pass catalogue.

        A scraper with no :class:`~cine_event_bot.io.scrapers.base.VenuePassSource`
        capability is silently skipped; a failing fetch is logged so one
        source's catalogue never aborts the run, matching every other
        best-effort step in this pipeline.
        """
        for scraper in self._scrapers:
            if not isinstance(scraper, VenuePassSource):
                continue
            try:
                passes = await scraper.fetch_venue_passes(client)
            except Exception:
                logger.exception(
                    "venue passes fetch failed",
                    extra={"ctx": {"source": scraper.source.value}},
                )
                continue
            await self._repository.update_venue_passes(passes)

    async def _apply_venue_details(self, client: httpx.AsyncClient) -> None:
        """Enrich stored venues from every scraper's optional detail source.

        A scraper with no :class:`~cine_event_bot.io.scrapers.base.VenueDetailSource`
        capability is silently skipped; a failing fetch is logged so one
        source's detail never aborts the run, matching
        :meth:`_apply_venue_passes`.
        """
        for scraper in self._scrapers:
            if not isinstance(scraper, VenueDetailSource):
                continue
            try:
                details = await scraper.fetch_venue_details(client)
            except Exception:
                logger.exception(
                    "venue details fetch failed",
                    extra={"ctx": {"source": scraper.source.value}},
                )
                continue
            await self._repository.update_venue_details(details)

    async def _apply_film_ratings(self, client: httpx.AsyncClient) -> None:
        """Enrich already-stored films with ratings from an optional source.

        A scraper with no :class:`~cine_event_bot.io.scrapers.base.FilmRatingSource`
        capability is silently skipped; a failing fetch is logged so one
        source's ratings never abort the run, matching
        :meth:`_apply_venue_passes` and :meth:`_apply_venue_details`.
        """
        for scraper in self._scrapers:
            if not isinstance(scraper, FilmRatingSource):
                continue
            try:
                ratings = await scraper.fetch_film_ratings(client)
            except Exception:
                logger.exception(
                    "film ratings fetch failed",
                    extra={"ctx": {"source": scraper.source.value}},
                )
                continue
            await self._repository.update_film_ratings(ratings)


def _log_specialness_verdict(
    event: ScreeningEvent, context: FilmContext, verdict: SpecialnessVerdict
) -> None:
    """Log one classified screening's full feature vector and verdict.

    Logged for every screening the classifier evaluates, whether or not a
    rule fired — a usable feedback-loop dataset needs negatives as well as
    positives (see the module docstring).
    """
    logger.info(
        "specialness verdict",
        extra={
            "ctx": {
                "event_id": event.id,
                "title": event.film.title,
                "venue": event.venue.name,
                "venue_kind": event.venue.kind.value,
                "release_year": event.film.release_year,
                "has_team_present": event.has_team_present,
                "has_cycle_name": event.cycle_name is not None,
                "distinct_venue_count": context.distinct_venue_count,
                "weekly_showing_count": context.weekly_showing_count,
                "is_special": verdict.is_special,
                "reasons": verdict.reasons,
                "confidence": verdict.confidence,
            }
        },
    )
