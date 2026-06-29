"""Scraper for Le Forum des images (forumdesimages.fr).

The ``/agenda`` page server-renders every upcoming screening as a self-contained
``session-calendar-teaser-item`` card holding the cycle, title, director(s),
date/time and session type — so a single page fetch is enough, with no detail
pages to follow.

The site is classic HTML (Level 4 in ``docs/scraping-strategy.md``): the scraper
gathers each card's text and an injected :class:`EventExtractor` (LLM) structures
it. The card's date omits the year ("Mardi 7 juillet à 20h"), so the raw text
carries a reference date the LLM uses to resolve the next upcoming occurrence.
"""

from datetime import date

import httpx
from bs4 import BeautifulSoup, Tag

from cine_event_bot.core.models import ScreeningEvent, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import RawListing, gather_events, structure_via_llm

_VENUE = "Le Forum des images"
_AGENDA_URL = "https://www.forumdesimages.fr/agenda"
_BASE_URL = "https://www.forumdesimages.fr"
_ITEM_SELECTOR = "article.session-calendar-teaser-item"


def _text(node: Tag | None) -> str:
    """Return a node's collapsed text, or an empty string when absent."""
    return node.get_text(" ", strip=True) if node is not None else ""


class ForumDesImagesScraper:
    """Scrapes screenings from the forumdesimages.fr agenda."""

    def __init__(self, extractor: EventExtractor) -> None:
        """Bind the scraper to the LLM extractor it structures text with.

        Args:
            extractor: LLM-backed extractor turning listing text into events.
        """
        self._extractor = extractor

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        return Source.FORUM_DES_IMAGES

    def parse_listings(self, html: str, *, reference_date: date) -> list[RawListing]:
        """Extract one raw listing per agenda card.

        Args:
            html: HTML of the forumdesimages.fr agenda page.
            reference_date: Date the LLM resolves year-less dates against,
                embedded in each listing's text.

        Returns:
            One :class:`RawListing` per session card on the page.
        """
        soup = BeautifulSoup(html, "html.parser")
        listings: list[RawListing] = []
        for card in soup.select(_ITEM_SELECTOR):
            listing = self._card_listing(card, reference_date)
            if listing is not None:
                listings.append(listing)
        return listings

    def _card_listing(self, card: Tag, reference_date: date) -> RawListing | None:
        """Build a raw listing from one agenda card, or None if it has no link."""
        heading = card.select_one(".teaser-content h3 a")
        href = heading.get("href") if isinstance(heading, Tag) else None
        if not isinstance(href, str):
            return None
        parts = [
            _VENUE,
            f"Reference date: {reference_date.isoformat()}",
            _text(card.select_one(".cycle-name")),
            _text(heading),
            _text(card.select_one(".directors")),
            _text(card.select_one(".field-datetime")),
            _text(card.select_one(".field-type-session")),
        ]
        raw_text = "\n".join(part for part in parts if part)
        return RawListing(
            source=Source.FORUM_DES_IMAGES,
            source_url=f"{_BASE_URL}{href}",
            raw_text=raw_text,
        )

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[ScreeningEvent]:
        """Fetch the agenda and structure each card into an event.

        Cards are structured concurrently (bounded); progress is reported per
        card. A card whose extraction fails is logged and skipped so one bad card
        never aborts the run.

        Args:
            client: Shared async HTTP client used for the request.
            reporter: Progress reporter for live display.

        Returns:
            One unpersisted :class:`ScreeningEvent` per successfully extracted
            card.
        """
        response = await client.get(_AGENDA_URL)
        response.raise_for_status()
        listings = self.parse_listings(response.text, reference_date=date.today())

        async def extract(listing: RawListing) -> ScreeningEvent | None:
            return await structure_via_llm(self._extractor, listing)

        return await gather_events(
            listings, extract, reporter=reporter, source=self.source.value
        )
