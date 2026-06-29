"""Scraper abstractions shared by every source.

A scraper's job is to turn one source's website into a list of
:class:`ScreeningEvent` ready to persist. *How* it gets there is an internal
detail: a source serving unstructured text (e.g. cinematheque.fr) runs the text
through the LLM extractor, while a source already serving structured JSON (e.g.
the Next.js sources) maps it directly. The pipeline stays agnostic to that
difference — it only ever calls :meth:`SourceScraper.fetch_events`.

Adding a source means writing one class that satisfies :class:`SourceScraper`
and registering it (see ``io/scrapers/__init__.py``); nothing else changes.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar, runtime_checkable

import httpx

from cine_event_bot.core.models import ScreeningEvent, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)

# Bounded concurrency for per-item work (detail fetches + LLM extraction). Keeps
# the source responsive and parallelizes I/O without flooding Ollama.
_CONCURRENCY = 5

_Item = TypeVar("_Item")


@dataclass(frozen=True, slots=True)
class RawListing:
    """One screening announcement as raw text, ready for LLM extraction.

    Used internally by text-based scrapers to carry a screening's text from the
    parsing step to the LLM extraction step; it is not part of the scraper
    contract.

    Attributes:
        source: The source this listing was scraped from.
        source_url: Direct link to the announcement.
        raw_text: Human-readable text describing the screening.
    """

    source: Source
    source_url: str
    raw_text: str


async def structure_via_llm(
    extractor: EventExtractor, listing: RawListing
) -> ScreeningEvent | None:
    """Structure one raw listing into an event via the LLM.

    Shared by every text-based scraper: a listing whose extraction fails is
    logged and skipped (returns None) so one bad listing never aborts a run.

    Args:
        extractor: LLM-backed extractor turning listing text into events.
        listing: The raw listing to structure.

    Returns:
        The structured :class:`ScreeningEvent`, or None when extraction failed.
    """
    try:
        extracted = await extractor.extract(listing.raw_text)
    except Exception:
        logger.exception(
            "llm extraction failed",
            extra={"ctx": {"source": listing.source.value, "url": listing.source_url}},
        )
        return None
    return ScreeningEvent.from_extracted(
        extracted, source=listing.source, source_url=listing.source_url
    )


async def gather_events(
    items: Sequence[_Item],
    worker: Callable[[_Item], Awaitable[ScreeningEvent | None]],
    *,
    reporter: ProgressReporter,
    source: str,
) -> list[ScreeningEvent]:
    """Run ``worker`` over ``items`` concurrently, reporting progress per item.

    The total is reported up front (so the bar is determinate from the start),
    each completed item advances it, and ``None`` results (failed extractions)
    are dropped. Concurrency is bounded by :data:`_CONCURRENCY`.

    Args:
        items: The per-item inputs (URLs, listings, ...).
        worker: Async function turning one item into an event or ``None``.
        reporter: Progress reporter to drive the live display.
        source: The source value, for the reporter.

    Returns:
        The successfully produced events, order not guaranteed.
    """
    reporter.events_fetched(source, len(items))
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def run(item: _Item) -> ScreeningEvent | None:
        async with semaphore:
            event = await worker(item)
        reporter.event_processed(source)
        return event

    results = await asyncio.gather(*(run(item) for item in items))
    return [event for event in results if event is not None]


@runtime_checkable
class SourceScraper(Protocol):
    """A source able to yield persistable screening events."""

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        ...

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[ScreeningEvent]:
        """Fetch the source and return its screenings ready to persist.

        Implementations report progress via ``reporter`` during their slow work
        (``events_fetched`` once the total is known, then ``event_processed``
        per item) so the live bar is determinate.

        Args:
            client: Shared async HTTP client used for every request.
            reporter: Progress reporter for live display.

        Returns:
            One unpersisted :class:`ScreeningEvent` per announced screening.
        """
        ...
