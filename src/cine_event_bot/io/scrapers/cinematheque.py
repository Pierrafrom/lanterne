"""Scraper for La Cinémathèque française (cinematheque.fr).

The homepage server-renders the upcoming screenings as ``a.event`` cards
linking to per-screening detail pages. Each detail page carries the full date,
room, cycle (retrospective) name, and film — the reliable source of truth — so
the scraper reads the index for the links, then each detail page for the text.
"""

import httpx
from bs4 import BeautifulSoup, Tag

from cine_event_bot.core.models import ScreeningEvent, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import RawListing, gather_events, structure_via_llm

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

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[ScreeningEvent]:
        """Fetch the index then each detail page, structured into events.

        Detail pages are fetched and structured concurrently (bounded); progress
        is reported per screening. A listing whose extraction fails is logged and
        skipped so one bad page never aborts the run.

        Args:
            client: Shared async HTTP client used for every request.
            reporter: Progress reporter for live display.

        Returns:
            One unpersisted :class:`ScreeningEvent` per successfully extracted
            screening linked from the index.
        """
        index_html = await _fetch_text(client, _INDEX_URL)
        urls = self.parse_index(index_html)

        async def extract(url: str) -> ScreeningEvent | None:
            detail_html = await _fetch_text(client, url)
            listing = self.parse_detail(detail_html, url)
            return await structure_via_llm(self._extractor, listing)

        return await gather_events(
            urls, extract, reporter=reporter, source=self.source.value
        )
