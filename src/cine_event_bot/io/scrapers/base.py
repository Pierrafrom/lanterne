"""Scraper abstractions shared by every source.

A scraper's only job is to turn a source's website into a list of
:class:`RawListing` — one blob of human-readable text per announced screening.
Structuring that text into a typed event is the LLM extractor's job (see
``io/llm.py``), so scrapers stay free of any domain parsing beyond locating and
concatenating the relevant text.

Adding a source means writing one class that satisfies :class:`SourceScraper`
and registering it (see ``io/scrapers/__init__.py``); nothing else changes.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from cine_event_bot.core.models import Source
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RawListing:
    """One screening announcement as raw text, ready for LLM extraction.

    Attributes:
        source: The source this listing was scraped from.
        source_url: Direct link to the announcement.
        raw_text: Human-readable text describing the screening.
    """

    source: Source
    source_url: str
    raw_text: str


@runtime_checkable
class SourceScraper(Protocol):
    """A source able to yield raw screening listings."""

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        ...

    async def fetch_listings(self, client: httpx.AsyncClient) -> list[RawListing]:
        """Fetch and parse the source into raw listings.

        Args:
            client: Shared async HTTP client used for every request.

        Returns:
            One :class:`RawListing` per announced screening.
        """
        ...


class PendingScraper:
    """Registered placeholder for a source not parsed against live HTML yet.

    Lets the source appear in the registry and the pipeline run end to end
    without it, while making the missing implementation explicit in the logs
    rather than silently absent.
    """

    def __init__(self, source: Source) -> None:
        """Bind the placeholder to the source it stands in for.

        Args:
            source: The source whose scraper is not implemented yet.
        """
        self._source = source

    @property
    def source(self) -> Source:
        """The source this placeholder stands in for."""
        return self._source

    async def fetch_listings(
        self,
        client: httpx.AsyncClient,  # noqa: ARG002 — required by SourceScraper protocol
    ) -> list[RawListing]:
        """Return no listings and log that the scraper is pending.

        Args:
            client: Unused; present to satisfy the scraper protocol.

        Returns:
            An empty list.
        """
        logger.warning(
            "scraper not implemented yet",
            extra={"ctx": {"source": self._source.value}},
        )
        return []
