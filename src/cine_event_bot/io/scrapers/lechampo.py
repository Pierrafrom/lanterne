"""Scraper for Le Champo (cinema-lechampo.com).

The ciné-clubs page is a single hand-authored CMS article, not a feed of
distinct cards: each cycle (B.O. Ciné-club, Les Rencontres de l'Histoire,
Les Lundis Hongrois...) is a rich-text block naming the cycle, and — when a
next screening is announced — a line marked with a "📍" pin giving its date,
time, and film. A cycle with no upcoming date announced (e.g. between
seasons) has no pin and is skipped, rather than guessed at.

The pin is the stable anchor (a human-authored convention, not a generated
CSS class), and the cycle's own text block already carries both the pin line
and its surrounding description; the "Réservation" booking link, when one
exists, sits in the immediately following sibling block.
"""

from datetime import date

import httpx
from bs4 import BeautifulSoup, Tag

from cine_event_bot.core.models import Sighting, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import RawListing, gather_events, structure_via_llm

_VENUE = "Le Champo"
_CINECLUBS_URL = "https://www.cinema-lechampo.com/evenements/cine-clubs.html"
_PIN = "📍"
_RESERVATION_PREFIX = "Réservation"


def _text(node: Tag | None) -> str:
    """Return a node's collapsed text, or an empty string when absent."""
    return node.get_text(" ", strip=True) if node is not None else ""


class LeChampoScraper:
    """Scrapes the ciné-clubs cycles from cinema-lechampo.com."""

    def __init__(self, extractor: EventExtractor) -> None:
        """Bind the scraper to the LLM extractor it structures text with.

        Args:
            extractor: LLM-backed extractor turning listing text into events.
        """
        self._extractor = extractor

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        return Source.LE_CHAMPO

    def parse_listings(self, html: str, *, reference_date: date) -> list[RawListing]:
        """Extract one raw listing per cycle with an announced next screening.

        Args:
            html: HTML of the cinema-lechampo.com ciné-clubs page.
            reference_date: Date the LLM resolves year-less dates against,
                embedded in each listing's text.

        Returns:
            One :class:`RawListing` per cycle carrying a "📍"-marked date.
        """
        soup = BeautifulSoup(html, "html.parser")
        listings: list[RawListing] = []
        for panel in soup.select("div.uk-panel.uk-margin"):
            listing = self._listing(panel, reference_date)
            if listing is not None:
                listings.append(listing)
        return listings

    def _listing(self, panel: Tag, reference_date: date) -> RawListing | None:
        """Build a listing from one cycle's panel, or None without an announced date."""
        text = _text(panel)
        if _PIN not in text:
            return None
        cycle_name = _text(panel.select_one("h3"))
        parts = [
            _VENUE,
            f"Reference date: {reference_date.isoformat()}",
            cycle_name,
            text,
        ]
        raw_text = "\n".join(part for part in parts if part)
        return RawListing(
            source=Source.LE_CHAMPO,
            source_url=_CINECLUBS_URL,
            raw_text=raw_text,
            booking_url=_booking_url(panel),
        )

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[Sighting]:
        """Fetch the ciné-clubs page and structure each announced cycle.

        Cycles are structured concurrently (bounded); progress is reported
        per cycle. A cycle whose extraction fails is logged and skipped so one
        bad entry never aborts the run.

        Args:
            client: Shared async HTTP client used for the request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`Sighting` per successfully extracted cycle.
        """
        response = await client.get(_CINECLUBS_URL)
        response.raise_for_status()
        listings = self.parse_listings(response.text, reference_date=date.today())

        async def extract(listing: RawListing) -> Sighting | None:
            return await structure_via_llm(self._extractor, listing)

        return await gather_events(
            listings, extract, reporter=reporter, source=self.source.value
        )


def _booking_url(panel: Tag) -> str | None:
    """Return the "Réservation" link from the panel's next sibling block, if any."""
    sibling = panel.find_next_sibling("div")
    if not isinstance(sibling, Tag):
        return None
    for anchor in sibling.select("a[href]"):
        if anchor.get_text(strip=True).startswith(_RESERVATION_PREFIX):
            href = anchor.get("href")
            return href if isinstance(href, str) else None
    return None
