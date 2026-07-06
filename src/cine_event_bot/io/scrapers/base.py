"""Scraper abstractions shared by every source.

A scraper's job is to turn one source's website into a list of
:class:`Sighting` objects ready for ingestion. *How* it gets there is an
internal detail: a source serving unstructured text (e.g. cinematheque.fr) runs
the text through the LLM extractor, while a source already serving structured
JSON (e.g. the Next.js sources) maps it directly. The pipeline stays agnostic
to that difference — it only ever calls :meth:`SourceScraper.fetch_events`.

Adding a source means writing one class that satisfies :class:`SourceScraper`
and registering it (see ``io/scrapers/__init__.py``); nothing else changes.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar, runtime_checkable

import httpx

from cine_event_bot.core.models import Sighting, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.core.validation import find_extraction_issues
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
) -> Sighting | None:
    """Structure one raw listing into a sighting via the LLM.

    Shared by every text-based scraper: a listing whose extraction fails or
    yields implausible values (hallucinated date, blank field — see
    ``core/validation.py``) is logged and skipped (returns None) so one bad
    listing never aborts a run.

    Args:
        extractor: LLM-backed extractor turning listing text into events.
        listing: The raw listing to structure.

    Returns:
        The structured :class:`Sighting`, or None when extraction failed or
        was rejected.
    """
    try:
        extracted = await extractor.extract(listing.raw_text)
    except Exception:
        logger.exception(
            "llm extraction failed",
            extra={"ctx": {"source": listing.source.value, "url": listing.source_url}},
        )
        return None
    issues = find_extraction_issues(extracted, now=datetime.now(UTC))
    if issues:
        logger.warning(
            "extraction rejected",
            extra={
                "ctx": {
                    "source": listing.source.value,
                    "url": listing.source_url,
                    "issues": issues,
                }
            },
        )
        return None
    return Sighting(
        extracted=extracted, source=listing.source, source_url=listing.source_url
    )


async def gather_events(
    items: Sequence[_Item],
    worker: Callable[[_Item], Awaitable[Sighting | None]],
    *,
    reporter: ProgressReporter,
    source: str,
) -> list[Sighting]:
    """Run ``worker`` over ``items`` concurrently, reporting progress per item.

    The total is reported up front (so the bar is determinate from the start),
    each completed item advances it, and ``None`` results (failed extractions)
    are dropped. Concurrency is bounded by :data:`_CONCURRENCY`.

    Args:
        items: The per-item inputs (URLs, listings, ...).
        worker: Async function turning one item into a sighting or ``None``.
        reporter: Progress reporter to drive the live display.
        source: The source value, for the reporter.

    Returns:
        The successfully produced sightings, order not guaranteed.
    """
    reporter.events_fetched(source, len(items))
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def run(item: _Item) -> Sighting | None:
        async with semaphore:
            sighting = await worker(item)
        reporter.event_processed(source)
        return sighting

    results = await asyncio.gather(*(run(item) for item in items))
    return [sighting for sighting in results if sighting is not None]


@runtime_checkable
class SourceScraper(Protocol):
    """A source able to yield screening sightings ready for ingestion."""

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        ...

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[Sighting]:
        """Fetch the source and return its screenings ready for ingestion.

        Implementations report progress via ``reporter`` during their slow work
        (``events_fetched`` once the total is known, then ``event_processed``
        per item) so the live bar is determinate.

        Args:
            client: Shared async HTTP client used for every request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`Sighting` per announced screening.
        """
        ...
