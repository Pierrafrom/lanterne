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

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from cine_event_bot.core.models import ScreeningEvent, Source
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)


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


@runtime_checkable
class SourceScraper(Protocol):
    """A source able to yield persistable screening events."""

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        ...

    async def fetch_events(self, client: httpx.AsyncClient) -> list[ScreeningEvent]:
        """Fetch the source and return its screenings ready to persist.

        Args:
            client: Shared async HTTP client used for every request.

        Returns:
            One unpersisted :class:`ScreeningEvent` per announced screening.
        """
        ...
