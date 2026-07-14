"""Scraper for L'Officiel des spectacles (offi.fr), the Île-de-France suburb complement.

Paris intra-muros is covered by Paris Ciné Info's 78-venue network (see
``docs/decisions/0008-drop-allocine-width-source.md``); offi.fr fills the gap
the user explicitly asked for — the IDF suburbs — via one listing page per
department (``/cinema/<slug>.html``, paginated), each linking every cinema in
it. Paris's own arrondissement pages exist on the same site but are
deliberately not scraped here, to avoid re-covering ground Paris Ciné Info
already owns; see ``docs/coverage-matrix.md`` for the confirmed venue counts
(25 in Seine-Saint-Denis alone) and the two-pillar rationale.

Each venue's own page (``/cinema/<venue-slug>.html``) is a Level 4 HTML page
in ``docs/scraping-strategy.md``'s taxonomy — a classic server-rendered DOM,
no embedded JSON — but, unlike every other Level 4 source in this codebase,
it is mapped **without an LLM**. Two things make that possible here where it
isn't for e.g. Cinémathèque or Le Forum des images:

- The programme is a fully tabular grid (``itemscope
  itemtype="schema.org/Movie"`` tiles, one ``HH:MM`` badge per showtime), not
  prose an LLM needs to interpret — the same "don't run an LLM over already
  structured data" rule that keeps Première Projo and MK2 off the LLM path
  (see ``docs/decisions/0004-rsc-extraction-and-pipeline.md``) applies
  equally to a fully tabular DOM.
- The date is never printed with a year: each page shows eight day tabs
  (``#t_0``..``#t_7``, e.g. "Lundi 13 Juillet"). Verified against a live page
  (``docs/coverage-matrix.md``): tab ``#t_0`` is always the page's fetch date
  and each following tab is exactly one day later — so the date is computed
  from the fetch date and the tab index, never parsed from the day-name/
  day-number text. This is more robust than year-less-date text parsing
  (contrast Le Forum des images/La Villette, which pass a reference date to
  the LLM and ask it to resolve a year-less prose date itself — a pattern
  confirmed unreliable live for the now-retired ``lechampo.py``, see
  [ADR 0012](../../../docs/decisions/0012-retire-lechampo.md); not yet
  audited for these two).

offi.fr carries no specialness signal of its own (no equivalent of Paris
Ciné Info's ``com`` field or Première Projo's ``avpType``) — every scraped
showing is stored as an ordinary screening (``event_type=None``), left for
the specialness classification pipeline (``core/specialness/``, not yet
implemented) to evaluate.
"""

import asyncio
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag

from cine_event_bot.core.models import ExtractedEvent, Sighting, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)

_SITE_URL = "https://www.offi.fr"

# One listing page per Île-de-France suburb department — Paris intra-muros is
# already covered by Paris Ciné Info (see module docstring). "essonnne" is
# offi.fr's own URL slug, typo and all — confirmed live, "essonne.html" 404s.
_DEPARTMENT_SLUGS = (
    "seine-saint-denis",
    "hauts-de-seine",
    "val-de-marne",
    "seine-et-marne",
    "yvelines",
    "essonnne",
    "val-d-oise",
)

_DAY_TAB_COUNT = 8
# Bounded concurrency for the per-venue page fetches, mirroring base.py's
# per-item concurrency bound (kept local: this scraper's work unit is a page
# fetch yielding many sightings, not the one-item-to-one-sighting shape
# gather_events assumes).
_CONCURRENCY = 5

# The API's showtime badges are naive Paris local time, not UTC.
_PARIS = ZoneInfo("Europe/Paris")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class OffiScraper:
    """Scrapes every screening across offi.fr's Île-de-France suburb network."""

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        return Source.OFFI

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[Sighting]:
        """Discover every suburb venue, then scrape each venue's programme.

        Discovery (walking each department's paginated listing) runs first
        and is not itself progress-reported, matching every other scraper's
        convention of a determinate bar only once the real total is known —
        here, once every venue page is identified. Venue pages are then
        fetched with bounded concurrency; a page that fails to load is
        logged and skipped so one broken venue never aborts the run.

        Args:
            client: Shared async HTTP client used for every request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`Sighting` per showtime found across every venue.
        """
        venue_urls = await self._discover_venue_urls(client)
        reference_date = date.today()
        semaphore = asyncio.Semaphore(_CONCURRENCY)

        async def fetch_venue(url: str) -> list[Sighting]:
            async with semaphore:
                sightings = await self._fetch_venue_page(client, url, reference_date)
            reporter.event_processed(self.source.value)
            return sightings

        reporter.events_fetched(self.source.value, len(venue_urls))
        results = await asyncio.gather(*(fetch_venue(url) for url in venue_urls))
        return [sighting for sightings in results for sighting in sightings]

    async def _discover_venue_urls(self, client: httpx.AsyncClient) -> list[str]:
        """Return every venue page URL linked from any department's listing."""
        urls: set[str] = set()
        for slug in _DEPARTMENT_SLUGS:
            urls.update(await self._department_venue_urls(client, slug))
        return sorted(urls)

    async def _department_venue_urls(
        self, client: httpx.AsyncClient, slug: str
    ) -> set[str]:
        """Return every venue URL for one department, walking its pagination."""
        base_url = f"{_SITE_URL}/cinema/{slug}.html"
        html = await self._get_text(client, base_url, ctx={"slug": slug})
        if html is None:
            return set()
        urls = set(_parse_venue_urls(html))
        for page in range(2, _max_page(html) + 1):
            page_html = await self._get_text(
                client,
                base_url,
                params={"npage": page},
                ctx={"slug": slug, "npage": page},
            )
            if page_html is not None:
                urls.update(_parse_venue_urls(page_html))
        return urls

    async def _fetch_venue_page(
        self, client: httpx.AsyncClient, url: str, reference_date: date
    ) -> list[Sighting]:
        """Fetch and parse one venue's programme page, or [] on failure."""
        html = await self._get_text(client, url, ctx={"url": url})
        if html is None:
            return []
        return parse_venue_page(html, reference_date=reference_date)

    async def _get_text(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        ctx: dict[str, object],
        params: dict[str, int] | None = None,
    ) -> str | None:
        """GET a page's text, logging and returning None rather than raising."""
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning("offi.fr page fetch failed", extra={"ctx": ctx})
            return None
        return response.text


def _max_page(html: str) -> int:
    """Return the department listing's highest pagination page, or 1 without a pager."""
    soup = BeautifulSoup(html, "html.parser")
    pages = []
    for tag in soup.select("ul.pagination a[data-page]"):
        page = tag.get("data-page")
        if isinstance(page, str) and page.isdigit():
            pages.append(int(page))
    return max(pages, default=1)


def _parse_venue_urls(html: str) -> list[str]:
    """Return every per-venue page URL linked from one department listing page."""
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for link in soup.select("a.h2-26"):
        href = link.get("href")
        if isinstance(href, str):
            urls.append(href)
    return urls


def parse_venue_page(html: str, *, reference_date: date) -> list[Sighting]:
    """Extract every showing from one venue's programme page.

    See the module docstring for why the date is computed from
    ``reference_date`` and the tab index rather than parsed from the
    day-name/day-number text.

    Args:
        html: HTML of one offi.fr ``/cinema/<slug>.html`` venue page.
        reference_date: The date the page was fetched on (Europe/Paris),
            i.e. the date of tab ``#t_0``.

    Returns:
        One :class:`Sighting` per showtime found, each an ordinary screening
        (``event_type=None`` — see the module docstring).
    """
    soup = BeautifulSoup(html, "html.parser")
    venue_name = _text(soup.select_one('h1[itemprop="name"]'))
    if not venue_name:
        return []
    sightings: list[Sighting] = []
    for day_offset in range(_DAY_TAB_COUNT):
        pane = soup.select_one(f"#t_{day_offset}")
        if pane is None:
            continue
        showing_date = reference_date + timedelta(days=day_offset)
        for movie in pane.select('[itemtype="http://schema.org/Movie"]'):
            sightings.extend(_movie_sightings(movie, venue_name, showing_date))
    return sightings


def _movie_sightings(movie: Tag, venue_name: str, showing_date: date) -> list[Sighting]:
    """Build one sighting per showtime badge found in one movie tile."""
    title = _text(movie.select_one(".event-title"))
    if not title:
        return []
    detail_link = movie.select_one('a[itemprop="url"]')
    href = detail_link.get("href") if detail_link is not None else None
    source_url = href if isinstance(href, str) else _SITE_URL
    sightings = []
    for badge in movie.select(".event-times-container .event-times"):
        starts_at = _combine(showing_date, badge.get_text(strip=True))
        if starts_at is None:
            continue
        sightings.append(
            Sighting(
                extracted=ExtractedEvent(
                    title=title, venue=venue_name, starts_at=starts_at
                ),
                source=Source.OFFI,
                source_url=source_url,
            )
        )
    return sightings


def _combine(showing_date: date, time_text: str) -> datetime | None:
    """Combine a calendar date with an "HH:MM" badge into a UTC datetime."""
    if not _TIME_RE.match(time_text):
        return None
    hour, minute = (int(part) for part in time_text.split(":"))
    naive = datetime.combine(showing_date, time(hour=hour, minute=minute))
    return naive.replace(tzinfo=_PARIS).astimezone(UTC)


def _text(node: Tag | None) -> str:
    """Return a node's collapsed text, or an empty string when absent."""
    return node.get_text(" ", strip=True) if node is not None else ""
