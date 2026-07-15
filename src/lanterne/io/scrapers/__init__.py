"""Scraper registry — the single place sources are wired in.

:func:`build_scrapers` is the factory the ingestion pipeline calls to get every
source's scraper, injecting the LLM extractor into the text-based ones and the
account credentials into the authenticated one. Structured sources (Première
Projo, offi.fr) take no extractor; Paris Ciné Info is skipped entirely when
its credentials are not configured (see ``docs/setup.md``).

MK2, Le Louxor, and Le Champo are deliberately **not** registered here —
retired in favor of Paris Ciné Info's full coverage of all three, confirmed
live (not just "listed in the network") after the earlier live spot-check
turned out to have false negatives from fragmented venue naming and an
exact-timestamp dedup key; see
[ADR 0011](../../../docs/decisions/0011-retire-mk2-louxor.md) for MK2/Le
Louxor and [ADR 0012](../../../docs/decisions/0012-retire-lechampo.md) for Le
Champo. The ``Source.MK2``/``Source.LE_LOUXOR``/``Source.LE_CHAMPO`` enum
members and their historical ``EventSighting`` rows are kept — only the
scrapers producing new ones are removed.
"""

from lanterne.io.llm import EventExtractor
from lanterne.io.scrapers.base import RawListing, SourceScraper
from lanterne.io.scrapers.cinematheque import CinemathequeScraper
from lanterne.io.scrapers.fondationpathe import FondationPatheScraper
from lanterne.io.scrapers.forumdesimages import ForumDesImagesScraper
from lanterne.io.scrapers.lavillette import LaVilletteScraper
from lanterne.io.scrapers.offi import OffiScraper
from lanterne.io.scrapers.paris_cine_info import ParisCineInfoScraper
from lanterne.io.scrapers.premiereprojo import PremiereProjoScraper


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
        FondationPatheScraper(extractor),
        LaVilletteScraper(extractor),
        OffiScraper(),
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
    "FondationPatheScraper",
    "ForumDesImagesScraper",
    "LaVilletteScraper",
    "OffiScraper",
    "ParisCineInfoScraper",
    "PremiereProjoScraper",
    "RawListing",
    "SourceScraper",
    "build_scrapers",
]
