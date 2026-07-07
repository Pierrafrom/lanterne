"""Scraper for Le Louxor (cinemalouxor.fr).

The ``/evenements/`` index links to individual event pages
(``/events/<id>-<slug>/``). Each event page is a long-form curatorial
dossier, not one screening: a retrospective names several films, each
announced with a metadata line in the shape "France I 1996 I 1h53" and,
further down its own paragraph, a date/time preceded by a "→" arrow.

The metadata line is the stable anchor a per-film block is built around —
it is a template convention of this CMS, distinct enough (no page banner or
critic-quote line matches its shape) that the line immediately before it is
reliably that film's all-caps title, and a block runs from one title to the
line just before the next one.
"""

import re
from datetime import date

import httpx
from bs4 import BeautifulSoup

from cine_event_bot.core.models import Sighting, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import RawListing, gather_events, structure_via_llm

_VENUE = "Le Louxor"
_BASE_URL = "https://www.cinemalouxor.fr"
_EVENTS_URL = f"{_BASE_URL}/evenements/"
_EVENT_LINK = re.compile(r"^/events/\d+-")
_FILM_METADATA = re.compile(r"^.+ I \d{4} I ")


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    """Fetch a URL and return its body text, raising on HTTP error."""
    response = await client.get(url)
    response.raise_for_status()
    return response.text


class LeLouxorScraper:
    """Scrapes retrospective/event dossiers from cinemalouxor.fr."""

    def __init__(self, extractor: EventExtractor) -> None:
        """Bind the scraper to the LLM extractor it structures text with.

        Args:
            extractor: LLM-backed extractor turning listing text into events.
        """
        self._extractor = extractor

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        return Source.LE_LOUXOR

    def parse_index(self, html: str) -> list[str]:
        """Extract the absolute URLs of every event dossier on the index page.

        Args:
            html: HTML of the cinemalouxor.fr ``/evenements/`` page.

        Returns:
            Absolute event URLs, de-duplicated, in document order.
        """
        soup = BeautifulSoup(html, "html.parser")
        seen_hrefs: set[str] = set()
        urls: list[str] = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            if (
                isinstance(href, str)
                and _EVENT_LINK.match(href)
                and href not in seen_hrefs
            ):
                seen_hrefs.add(href)
                urls.append(f"{_BASE_URL}{href}")
        return urls

    def parse_detail(
        self, html: str, url: str, *, reference_date: date
    ) -> list[RawListing]:
        """Extract one raw listing per film screening in an event's dossier.

        Args:
            html: HTML of one ``/events/<id>-<slug>/`` page.
            url: The dossier's URL, kept as every listing's provenance.
            reference_date: Date the LLM resolves year-less dates against,
                embedded in each listing's text.

        Returns:
            One :class:`RawListing` per film whose block contains a "→"
            date marker; empty when the page has no recognizable film block.
        """
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "svg"]):
            tag.decompose()
        main = soup.select_one("main")
        if main is None:
            return []
        lines = [line for line in main.get_text("\n", strip=True).split("\n") if line]
        cycle_name = lines[1] if len(lines) > 1 else ""
        title_indexes = [
            index - 1
            for index, line in enumerate(lines)
            if index > 0 and _FILM_METADATA.match(line)
        ]
        listings: list[RawListing] = []
        for position, start in enumerate(title_indexes):
            end = (
                title_indexes[position + 1]
                if position + 1 < len(title_indexes)
                else len(lines)
            )
            block = lines[start:end]
            if not any("→" in line for line in block):
                continue
            parts = [
                _VENUE,
                f"Reference date: {reference_date.isoformat()}",
                cycle_name,
                "\n".join(block),
            ]
            listings.append(
                RawListing(
                    source=Source.LE_LOUXOR,
                    source_url=url,
                    raw_text="\n".join(part for part in parts if part),
                )
            )
        return listings

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[Sighting]:
        """Fetch every event dossier and structure each announced film.

        Dossiers are fetched sequentially (a handful per run); the LLM
        structuring step that follows is bounded and progress-reported, like
        every other text-based scraper.

        Args:
            client: Shared async HTTP client used for every request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`Sighting` per successfully extracted screening.
        """
        index_html = await _fetch_text(client, _EVENTS_URL)
        reference_date = date.today()
        listings: list[RawListing] = []
        for url in self.parse_index(index_html):
            detail_html = await _fetch_text(client, url)
            listings.extend(
                self.parse_detail(detail_html, url, reference_date=reference_date)
            )

        async def extract(listing: RawListing) -> Sighting | None:
            return await structure_via_llm(self._extractor, listing)

        return await gather_events(
            listings, extract, reporter=reporter, source=self.source.value
        )
