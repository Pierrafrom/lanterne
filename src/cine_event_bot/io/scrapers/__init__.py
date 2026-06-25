"""Scraper registry — the single place sources are wired in.

``SCRAPERS`` is the list the ingestion pipeline iterates over. Three of the four
sources are still :class:`PendingScraper` placeholders until their live-HTML
parsing is implemented; only La Cinémathèque française is fully wired.
"""

from cine_event_bot.core.models import Source
from cine_event_bot.io.scrapers.base import (
    PendingScraper,
    RawListing,
    SourceScraper,
)
from cine_event_bot.io.scrapers.cinematheque import CinemathequeScraper

SCRAPERS: list[SourceScraper] = [
    CinemathequeScraper(),
    PendingScraper(Source.PREMIERE_PROJO),
    PendingScraper(Source.SORTIRAPARIS),
    PendingScraper(Source.FORUM_DES_IMAGES),
]

__all__ = [
    "SCRAPERS",
    "CinemathequeScraper",
    "PendingScraper",
    "RawListing",
    "SourceScraper",
]
