"""Scraper for the Fondation Jérôme Seydoux-Pathé (fondation-jeromeseydoux-pathe.com).

The ``/agenda`` page mixes several kinds of items in one tile grid — film
screenings, thematic cycles (a curatorial essay with no screening time of its
own — each cycle's films already have their own dedicated tile), workshops,
exhibitions, guided tours. Each tile carries one or more semicolon-separated
category tags (e.g. ``SÉANCES``, ``SÉANCES ; JEUNE-PUBLIC``, ``CYCLES``,
``EXPOSITIONS À VENIR``); only tiles tagged ``SÉANCES`` are actual dated
screenings and are scraped.

Full dates are given with the year (``DD/MM/YYYY - HH:MM``), so — unlike
Le Forum des images or La Villette — no reference-date hint is needed, and
the tile's own text (title, director, year) is enough context with no
detail-page visit.
"""

import httpx
from bs4 import BeautifulSoup, Tag

from cine_event_bot.core.models import Sighting, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import RawListing, gather_events, structure_via_llm

_VENUE = "Fondation Jérôme Seydoux-Pathé"
_AGENDA_URL = "https://www.fondation-jeromeseydoux-pathe.com/agenda"
_SEANCES_TAG = "SÉANCES"


def _text(node: Tag | None) -> str:
    """Return a node's collapsed text, or an empty string when absent."""
    return node.get_text(" ", strip=True) if node is not None else ""


def _tags(tile: Tag) -> list[str]:
    """Return a tile's category tags, split on the ";" separator."""
    raw = _text(tile.select_one(".tags .text"))
    return [tag.strip() for tag in raw.split(";") if tag.strip()]


class FondationPatheScraper:
    """Scrapes special screenings from the Fondation Jérôme Seydoux-Pathé agenda."""

    def __init__(self, extractor: EventExtractor) -> None:
        """Bind the scraper to the LLM extractor it structures text with.

        Args:
            extractor: LLM-backed extractor turning listing text into events.
        """
        self._extractor = extractor

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        return Source.FONDATION_PATHE

    def parse_listings(self, html: str) -> list[RawListing]:
        """Extract one raw listing per screening tile on the agenda page.

        Args:
            html: HTML of the fondation-jeromeseydoux-pathe.com agenda page.

        Returns:
            One :class:`RawListing` per tile tagged "SÉANCES"; cycles,
            exhibitions, workshops, and tours are excluded.
        """
        soup = BeautifulSoup(html, "html.parser")
        listings: list[RawListing] = []
        for tile in soup.select("a.programmation-tile"):
            listing = self._listing(tile)
            if listing is not None:
                listings.append(listing)
        return listings

    def _listing(self, tile: Tag) -> RawListing | None:
        """Build a listing from one tile, or None when it is not a screening."""
        if _SEANCES_TAG not in _tags(tile):
            return None
        href = tile.get("href")
        if not isinstance(href, str):
            return None
        parts = [
            _VENUE,
            _text(tile.select_one(".dates")),
            _text(tile.select_one(".title")),
        ]
        return RawListing(
            source=Source.FONDATION_PATHE,
            source_url=href,
            raw_text="\n".join(part for part in parts if part),
        )

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[Sighting]:
        """Fetch the agenda and structure each screening tile.

        Tiles are structured concurrently (bounded); progress is reported per
        tile. A tile whose extraction fails is logged and skipped so one bad
        entry never aborts the run.

        Args:
            client: Shared async HTTP client used for the request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`Sighting` per successfully extracted screening.
        """
        response = await client.get(_AGENDA_URL)
        response.raise_for_status()
        listings = self.parse_listings(response.text)

        async def extract(listing: RawListing) -> Sighting | None:
            return await structure_via_llm(self._extractor, listing)

        return await gather_events(
            listings, extract, reporter=reporter, source=self.source.value
        )
