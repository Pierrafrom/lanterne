"""Scraper for Le Forum des images (forumdesimages.fr).

The ``/agenda`` page server-renders every upcoming screening as a self-contained
``session-calendar-teaser-item`` card holding the cycle, title, director(s),
date/time and session type — so a single page fetch is enough, with no detail
pages to follow.

The site is classic HTML (Level 4 in ``docs/scraping-strategy.md``): the scraper
gathers each card's text and an injected :class:`EventExtractor` (LLM) structures
it. The card's date omits the year ("Mardi 7 juillet à 20h"), so the day/month/
time are parsed with :data:`_DATETIME` and resolved deterministically via
``core/frenchdate.py`` — not trusted to the LLM's own arithmetic, which was
confirmed live to get this exact kind of resolution wrong (see
``docs/decisions/0012-retire-lechampo.md``). The reference date is still
embedded in the raw text as context for the LLM's classification, but the
resolved date always wins via ``structure_via_llm``'s ``known_starts_at``.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup, Tag

from cine_event_bot.core.frenchdate import resolve_next_occurrence, to_utc_datetime
from cine_event_bot.core.models import Sighting, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import RawListing, gather_events, structure_via_llm

_VENUE = "Le Forum des images"
_AGENDA_URL = "https://www.forumdesimages.fr/agenda"
_BASE_URL = "https://www.forumdesimages.fr"
_ITEM_SELECTOR = "article.session-calendar-teaser-item"
_DATETIME = re.compile(r"(\d{1,2}) (\w+) à (\d{1,2})h(\d{2})?")


def _text(node: Tag | None) -> str:
    """Return a node's collapsed text, or an empty string when absent."""
    return node.get_text(" ", strip=True) if node is not None else ""


@dataclass(frozen=True, slots=True)
class _ScheduledListing:
    """A raw listing paired with its deterministically-resolved start time.

    ``known_starts_at`` is ``None`` only when the card's datetime text does
    not match :data:`_DATETIME` (an unexpected site format) — the listing
    still goes through the LLM, degrading to its own date guess rather than
    being dropped outright.
    """

    listing: RawListing
    known_starts_at: datetime | None


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

    def parse_listings(
        self, html: str, *, reference_date: date
    ) -> list[_ScheduledListing]:
        """Extract one scheduled listing per agenda card.

        Args:
            html: HTML of the forumdesimages.fr agenda page.
            reference_date: Date the year-less card date is resolved
                against (see ``core/frenchdate.py::resolve_next_occurrence``),
                also embedded in each listing's text as LLM context.

        Returns:
            One :class:`_ScheduledListing` per session card on the page.
        """
        soup = BeautifulSoup(html, "html.parser")
        listings: list[_ScheduledListing] = []
        for card in soup.select(_ITEM_SELECTOR):
            listing = self._card_listing(card, reference_date)
            if listing is not None:
                listings.append(listing)
        return listings

    def _card_listing(
        self, card: Tag, reference_date: date
    ) -> _ScheduledListing | None:
        """Build a scheduled listing from one agenda card, or None if it has no link."""
        heading = card.select_one(".teaser-content h3 a")
        href = heading.get("href") if isinstance(heading, Tag) else None
        if not isinstance(href, str):
            return None
        datetime_text = _text(card.select_one(".field-datetime"))
        parts = [
            _VENUE,
            f"Reference date: {reference_date.isoformat()}",
            _text(card.select_one(".cycle-name")),
            _text(heading),
            _text(card.select_one(".directors")),
            datetime_text,
            _text(card.select_one(".field-type-session")),
        ]
        raw_text = "\n".join(part for part in parts if part)
        listing = RawListing(
            source=Source.FORUM_DES_IMAGES,
            source_url=f"{_BASE_URL}{href}",
            raw_text=raw_text,
        )
        return _ScheduledListing(
            listing=listing,
            known_starts_at=_resolve_known_starts_at(datetime_text, reference_date),
        )

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[Sighting]:
        """Fetch the agenda and structure each card into a sighting.

        Cards are structured concurrently (bounded); progress is reported per
        card. A card whose extraction fails is logged and skipped so one bad card
        never aborts the run.

        Args:
            client: Shared async HTTP client used for the request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`Sighting` per successfully extracted card.
        """
        response = await client.get(_AGENDA_URL)
        response.raise_for_status()
        listings = self.parse_listings(response.text, reference_date=date.today())

        async def extract(item: _ScheduledListing) -> Sighting | None:
            return await structure_via_llm(
                self._extractor, item.listing, known_starts_at=item.known_starts_at
            )

        return await gather_events(
            listings, extract, reporter=reporter, source=self.source.value
        )


def _resolve_known_starts_at(
    datetime_text: str, reference_date: date
) -> datetime | None:
    """Deterministically resolve a card's year-less datetime text to UTC.

    Args:
        datetime_text: The card's ``.field-datetime`` text (e.g.
            "Mardi 7 juillet à 20h").
        reference_date: Date "next occurrence" is resolved against.

    Returns:
        The resolved UTC datetime, or ``None`` when the text does not match
        the expected pattern (an unexpected site format).
    """
    match = _DATETIME.search(datetime_text)
    if match is None:
        return None
    day, month_name, hour, minute = match.groups()
    resolved_date = resolve_next_occurrence(int(day), month_name, reference_date)
    if resolved_date is None:
        return None
    return to_utc_datetime(resolved_date, int(hour), int(minute or 0))
