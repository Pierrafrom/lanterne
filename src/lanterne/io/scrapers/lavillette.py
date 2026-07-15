"""Scraper for La Villette's open-air cinema (lavillette.com).

A single seasonal page lists the whole summer programme, grouped under a
day-name heading ("Mercredi 22 juillet", no year) with two films per day: an
early "SÉANCE JEUNE PUBLIC" screening (18h00) and the main evening feature
(21h00) — a site-wide fixed schedule, not printed per film, so the presence
of the "SÉANCE JEUNE PUBLIC" label right before a title is what distinguishes
the two rather than position (a day with only one film would otherwise be
ambiguous).

Anchored on the film metadata line (", <director> • <year>"), the same
technique as Le Louxor: it is distinctive enough that the line right before
it is reliably that film's title, and it does not appear anywhere in the
page's other text (practical info, disclaimers).

The day heading's day+month (no year) is resolved deterministically via
``core/frenchdate.py`` rather than left to the LLM — asking the LLM to
resolve a year-less date itself is unreliable, confirmed live for the
now-retired ``lechampo.py`` (see ``docs/decisions/0012-retire-lechampo.md``).
The fixed 18h00/21h00 schedule means the time is already fully known in
code, so only the date needs resolving.

The page URL is specific to one year's edition (``cinema-en-plein-air-26``)
and needs updating for each new season.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup

from lanterne.core.frenchdate import resolve_next_occurrence, to_utc_datetime
from lanterne.core.models import Sighting, Source
from lanterne.core.progress import ProgressReporter
from lanterne.io.llm import EventExtractor
from lanterne.io.scrapers.base import RawListing, gather_events, structure_via_llm

_VENUE = "Cinéma en plein air de La Villette"
_PROGRAMME_URL = "https://www.lavillette.com/manifestations/cinema-en-plein-air-26/"
_DAY = re.compile(
    r"^(?:Lundi|Mardi|Mercredi|Jeudi|Vendredi|Samedi|Dimanche) (\d{1,2}) "
    r"(janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|"
    r"novembre|décembre)$"
)
_FILM_METADATA = re.compile(r"^, .+ • \d{4}$")
_YOUNG_AUDIENCE_LABEL = "SÉANCE JEUNE PUBLIC"
_YOUNG_AUDIENCE_TIME = "18h00"
_MAIN_FEATURE_TIME = "21h00"
_YOUNG_AUDIENCE_HOUR_MINUTE = (18, 0)
_MAIN_FEATURE_HOUR_MINUTE = (21, 0)


@dataclass(frozen=True, slots=True)
class _ScheduledListing:
    """A raw listing paired with its deterministically-resolved start time.

    ``known_starts_at`` is ``None`` only when the day heading's text does
    not match :data:`_DAY` (an unexpected site format) — the listing still
    goes through the LLM, degrading to its own date guess rather than being
    dropped outright.
    """

    listing: RawListing
    known_starts_at: datetime | None


class LaVilletteScraper:
    """Scrapes the open-air cinema programme from lavillette.com."""

    def __init__(self, extractor: EventExtractor) -> None:
        """Bind the scraper to the LLM extractor it structures text with.

        Args:
            extractor: LLM-backed extractor turning listing text into events.
        """
        self._extractor = extractor

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        return Source.LA_VILLETTE

    def parse_listings(
        self, html: str, *, reference_date: date
    ) -> list[_ScheduledListing]:
        """Extract one scheduled listing per announced screening.

        Args:
            html: HTML of the lavillette.com open-air programme page.
            reference_date: Date the year-less day heading is resolved
                against (see ``core/frenchdate.py::resolve_next_occurrence``),
                also embedded in each listing's text as LLM context.

        Returns:
            One :class:`_ScheduledListing` per film found under a day heading.
        """
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "svg"]):
            tag.decompose()
        main = soup.select_one("main") or soup.select_one("body")
        if main is None:
            return []
        lines = [line for line in main.get_text("\n", strip=True).split("\n") if line]
        return self._listings_from_lines(lines, reference_date)

    def _listings_from_lines(
        self, lines: list[str], reference_date: date
    ) -> list[_ScheduledListing]:
        """Build one listing per detected film title within its day's block."""
        day_indexes = [index for index, line in enumerate(lines) if _DAY.match(line)]
        title_indexes = [
            index - 1
            for index, line in enumerate(lines)
            if index > 0 and _FILM_METADATA.match(line)
        ]
        listings: list[_ScheduledListing] = []
        for position, start in enumerate(title_indexes):
            day_label = _current_day(day_indexes, lines, start)
            if day_label is None:
                continue
            is_young_audience = start > 0 and lines[start - 1] == _YOUNG_AUDIENCE_LABEL
            time = _YOUNG_AUDIENCE_TIME if is_young_audience else _MAIN_FEATURE_TIME
            hour, minute = (
                _YOUNG_AUDIENCE_HOUR_MINUTE
                if is_young_audience
                else _MAIN_FEATURE_HOUR_MINUTE
            )
            end = (
                title_indexes[position + 1]
                if position + 1 < len(title_indexes)
                else len(lines)
            )
            block = "\n".join(lines[start:end])
            parts = [
                _VENUE,
                f"Reference date: {reference_date.isoformat()}",
                f"{day_label} à {time}",
                block,
            ]
            listing = RawListing(
                source=Source.LA_VILLETTE,
                source_url=_PROGRAMME_URL,
                raw_text="\n".join(parts),
            )
            listings.append(
                _ScheduledListing(
                    listing=listing,
                    known_starts_at=_resolve_known_starts_at(
                        day_label, hour, minute, reference_date
                    ),
                )
            )
        return listings

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[Sighting]:
        """Fetch the programme page and structure each announced screening.

        Screenings are structured concurrently (bounded); progress is
        reported per screening. A screening whose extraction fails is logged
        and skipped so one bad entry never aborts the run.

        Args:
            client: Shared async HTTP client used for the request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`Sighting` per successfully extracted screening.
        """
        response = await client.get(_PROGRAMME_URL)
        response.raise_for_status()
        listings = self.parse_listings(response.text, reference_date=date.today())

        async def extract(item: _ScheduledListing) -> Sighting | None:
            return await structure_via_llm(
                self._extractor, item.listing, known_starts_at=item.known_starts_at
            )

        return await gather_events(
            listings, extract, reporter=reporter, source=self.source.value
        )


def _current_day(
    day_indexes: list[int], lines: list[str], title_index: int
) -> str | None:
    """Return the day heading text in effect at ``title_index``, if any."""
    current: str | None = None
    for day_index in day_indexes:
        if day_index > title_index:
            break
        current = lines[day_index]
    return current


def _resolve_known_starts_at(
    day_label: str, hour: int, minute: int, reference_date: date
) -> datetime | None:
    """Deterministically resolve a day heading + fixed time to a UTC datetime.

    Args:
        day_label: The day heading text (e.g. "Mercredi 22 juillet").
        hour: Local hour of the fixed schedule slot (18 or 21).
        minute: Local minute of the fixed schedule slot (always 0 here).
        reference_date: Date "next occurrence" is resolved against.

    Returns:
        The resolved UTC datetime, or ``None`` when the day heading does not
        match :data:`_DAY` (an unexpected site format).
    """
    match = _DAY.match(day_label)
    if match is None:
        return None
    day, month_name = match.groups()
    resolved_date = resolve_next_occurrence(int(day), month_name, reference_date)
    if resolved_date is None:
        return None
    return to_utc_datetime(resolved_date, hour, minute)
