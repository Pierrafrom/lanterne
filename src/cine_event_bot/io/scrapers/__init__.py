"""Scraper registry — the single place sources are wired in.

:func:`build_scrapers` is the factory the ingestion pipeline calls to get every
source's scraper, injecting the LLM extractor into the text-based ones and the
account credentials into the authenticated one. Structured sources (Première
Projo) take no extractor; Paris Ciné Info is skipped entirely when its
credentials are not configured (see ``docs/setup.md``).
"""

from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import RawListing, SourceScraper
from cine_event_bot.io.scrapers.cinematheque import CinemathequeScraper
from cine_event_bot.io.scrapers.forumdesimages import ForumDesImagesScraper
from cine_event_bot.io.scrapers.lechampo import LeChampoScraper
from cine_event_bot.io.scrapers.paris_cine_info import ParisCineInfoScraper
from cine_event_bot.io.scrapers.premiereprojo import PremiereProjoScraper


def build_scrapers(
    extractor: EventExtractor,
    *,
    paris_cine_info_login: str | None = None,
    paris_cine_info_password: str | None = None,
) -> list[SourceScraper]:
    """Build every configured source scraper, injecting dependencies where needed.

    Text-based sources receive the LLM extractor; structured sources (which map
    embedded JSON directly) take no extractor. Paris Ciné Info additionally
    needs account credentials and is omitted when they are not configured.

    Args:
        extractor: LLM-backed extractor used by text-based scrapers.
        paris_cine_info_login: paris-cine.info account email, if configured.
        paris_cine_info_password: paris-cine.info account password, if
            configured.

    Returns:
        One scraper per configured source, ready to be iterated by the
        pipeline.
    """
    scrapers: list[SourceScraper] = [
        CinemathequeScraper(extractor),
        PremiereProjoScraper(),
        ForumDesImagesScraper(extractor),
        LeChampoScraper(extractor),
    ]
    if paris_cine_info_login and paris_cine_info_password:
        scrapers.append(
            ParisCineInfoScraper(
                extractor, paris_cine_info_login, paris_cine_info_password
            )
        )
    return scrapers


__all__ = [
    "CinemathequeScraper",
    "ForumDesImagesScraper",
    "LeChampoScraper",
    "ParisCineInfoScraper",
    "PremiereProjoScraper",
    "RawListing",
    "SourceScraper",
    "build_scrapers",
]
