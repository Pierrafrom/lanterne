"""Scraper registry — the single place sources are wired in.

:func:`build_scrapers` is the factory the ingestion pipeline calls to get every
source's scraper, injecting the LLM extractor into the ones that need it. Three
of the four sources are still :class:`PendingScraper` placeholders until their
live parsing is implemented; only La Cinémathèque française is fully wired.
"""

from cine_event_bot.core.models import Source
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import (
    PendingScraper,
    RawListing,
    SourceScraper,
)
from cine_event_bot.io.scrapers.cinematheque import CinemathequeScraper


def build_scrapers(extractor: EventExtractor) -> list[SourceScraper]:
    """Build every source scraper, injecting the extractor where needed.

    Args:
        extractor: LLM-backed extractor used by text-based scrapers.

    Returns:
        One scraper per source, ready to be iterated by the pipeline.
    """
    return [
        CinemathequeScraper(extractor),
        PendingScraper(Source.PREMIERE_PROJO),
        PendingScraper(Source.SORTIRAPARIS),
        PendingScraper(Source.FORUM_DES_IMAGES),
    ]


__all__ = [
    "CinemathequeScraper",
    "PendingScraper",
    "RawListing",
    "SourceScraper",
    "build_scrapers",
]
