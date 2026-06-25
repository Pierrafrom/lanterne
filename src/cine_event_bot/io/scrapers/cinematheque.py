"""Scraper for La Cinémathèque française (cinematheque.fr).

The homepage server-renders the upcoming screenings as ``a.event`` cards
linking to per-screening detail pages. Each detail page carries the full date,
room, cycle (retrospective) name, and film — the reliable source of truth — so
the scraper reads the index for the links, then each detail page for the text.
"""

import httpx
from bs4 import BeautifulSoup, Tag

from cine_event_bot.core.models import ScreeningEvent, Source
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import RawListing
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)

_VENUE = "La Cinémathèque française"
_INDEX_URL = "https://www.cinematheque.fr/"
_DETAIL_PATH = "/seance/"


def _text(node: Tag | None) -> str:
    """Return a node's collapsed text, or an empty string when absent."""
    return node.get_text(" ", strip=True) if node is not None else ""


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    """Fetch a URL and return its body text, raising on HTTP error."""
    response = await client.get(url)
    response.raise_for_status()
    return response.text


class CinemathequeScraper:
    """Scrapes upcoming special screenings from cinematheque.fr.

    The site serves unstructured text, so the scraper locates each screening's
    text and delegates structuring to an injected :class:`EventExtractor`.
    """

    def __init__(self, extractor: EventExtractor) -> None:
        """Bind the scraper to the LLM extractor it structures text with.

        Args:
            extractor: LLM-backed extractor turning listing text into events.
        """
        self._extractor = extractor

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        return Source.CINEMATHEQUE

    def parse_index(self, html: str) -> list[str]:
        """Extract the detail-page URLs of every screening on the index page.

        Args:
            html: HTML of the cinematheque.fr homepage.

        Returns:
            Absolute detail-page URLs, de-duplicated, in document order.
        """
        soup = BeautifulSoup(html, "html.parser")
        urls: list[str] = []
        for anchor in soup.select("a.event[href]"):
            href = anchor.get("href")
            if isinstance(href, str) and _DETAIL_PATH in href and href not in urls:
                urls.append(href)
        return urls

    def parse_detail(self, html: str, url: str) -> RawListing:
        """Build a raw listing from one screening's detail page.

        Args:
            html: HTML of a ``/seance/`` detail page.
            url: The detail page's URL, kept as the listing's provenance.

        Returns:
            A :class:`RawListing` whose text gathers venue, cycle, date, film.
        """
        soup = BeautifulSoup(html, "html.parser")
        parts = [
            _VENUE,
            _text(soup.select_one(".cycle")),
            _text(soup.select_one(".date")),
            _text(soup.select_one(".film")),
        ]
        raw_text = "\n".join(part for part in parts if part)
        return RawListing(source=Source.CINEMATHEQUE, source_url=url, raw_text=raw_text)

    async def fetch_events(self, client: httpx.AsyncClient) -> list[ScreeningEvent]:
        """Fetch the index then each detail page, structured into events.

        Each detail page's text is structured by the LLM extractor. A listing
        whose extraction fails is logged and skipped so one bad page never
        aborts the whole run.

        Args:
            client: Shared async HTTP client used for every request.

        Returns:
            One unpersisted :class:`ScreeningEvent` per successfully extracted
            screening linked from the index.
        """
        index_html = await _fetch_text(client, _INDEX_URL)
        events: list[ScreeningEvent] = []
        for url in self.parse_index(index_html):
            detail_html = await _fetch_text(client, url)
            listing = self.parse_detail(detail_html, url)
            event = await self._structure(listing)
            if event is not None:
                events.append(event)
        return events

    async def _structure(self, listing: RawListing) -> ScreeningEvent | None:
        """Structure one listing via the LLM, returning None on failure."""
        try:
            extracted = await self._extractor.extract(listing.raw_text)
        except Exception:
            logger.exception(
                "llm extraction failed",
                extra={
                    "ctx": {
                        "source": listing.source.value,
                        "url": listing.source_url,
                    }
                },
            )
            return None
        return ScreeningEvent.from_extracted(
            extracted, source=listing.source, source_url=listing.source_url
        )
