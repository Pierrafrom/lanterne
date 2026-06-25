"""Scraper registry — the single place sources are wired in.

:func:`build_scrapers` is the factory the ingestion pipeline calls to get every
source's scraper, injecting the LLM extractor into the text-based ones. The MVP
covers three sources; structured sources (Première Projo) take no extractor.
"""

from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import RawListing, SourceScraper
from cine_event_bot.io.scrapers.cinematheque import CinemathequeScraper
from cine_event_bot.io.scrapers.forumdesimages import ForumDesImagesScraper
from cine_event_bot.io.scrapers.premiereprojo import PremiereProjoScraper


def build_scrapers(extractor: EventExtractor) -> list[SourceScraper]:
    """Build every source scraper, injecting the extractor where needed.

    Text-based sources receive the LLM extractor; structured sources (which map
    embedded JSON directly) take no extractor.

    Args:
        extractor: LLM-backed extractor used by text-based scrapers.

    Returns:
        One scraper per MVP source, ready to be iterated by the pipeline.
    """
    return [
        CinemathequeScraper(extractor),
        PremiereProjoScraper(),
        ForumDesImagesScraper(extractor),
    ]


__all__ = [
    "CinemathequeScraper",
    "ForumDesImagesScraper",
    "PremiereProjoScraper",
    "RawListing",
    "SourceScraper",
    "build_scrapers",
]
