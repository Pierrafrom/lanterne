"""Scraper abstractions shared by every source.

A scraper's job is to turn one source's website into a list of
:class:`Sighting` objects ready for ingestion. *How* it gets there is an
internal detail: a source serving unstructured text (e.g. cinematheque.fr) runs
the text through the LLM extractor, while a source already serving structured
JSON (e.g. the Next.js sources) maps it directly. The pipeline stays agnostic
to that difference — it only ever calls :meth:`SourceScraper.fetch_events`.

Adding a source means writing one class that satisfies :class:`SourceScraper`
and registering it (see ``io/scrapers/__init__.py``); nothing else changes.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar, runtime_checkable

import httpx

from lanterne.core.models import RatingSource, Sighting, Source
from lanterne.core.progress import ProgressReporter
from lanterne.core.validation import find_extraction_issues
from lanterne.io.llm import EventExtractor
from lanterne.logging_config import get_logger

logger = get_logger(__name__)

# Bounded concurrency for per-item work (detail fetches + LLM extraction). Keeps
# the source responsive and parallelizes I/O without flooding Ollama.
_CONCURRENCY = 5

_Item = TypeVar("_Item")


@dataclass(frozen=True, slots=True)
class RawListing:
    """One screening announcement as raw text, ready for LLM extraction.

    Used internally by text-based scrapers to carry a screening's text from the
    parsing step to the LLM extraction step; it is not part of the scraper
    contract.

    Attributes:
        source: The source this listing was scraped from.
        source_url: Direct link to the announcement.
        raw_text: Human-readable text describing the screening.
        booking_url: Direct link to buy tickets, when the source provides one
            independently of the announcement page.
    """

    source: Source
    source_url: str
    raw_text: str
    booking_url: str | None = None


async def structure_via_llm(
    extractor: EventExtractor,
    listing: RawListing,
    *,
    known_starts_at: datetime | None = None,
) -> Sighting | None:
    """Structure one raw listing into a sighting via the LLM.

    Shared by every text-based scraper: a listing whose extraction fails or
    yields implausible values (hallucinated date, blank field — see
    ``core/validation.py``) is logged and skipped (returns None) so one bad
    listing never aborts a run.

    Args:
        extractor: LLM-backed extractor turning listing text into events.
        listing: The raw listing to structure.
        known_starts_at: When the caller already resolved the screening's
            start time deterministically (e.g.
            ``core/frenchdate.py::resolve_next_occurrence`` for a year-less
            date), it replaces the LLM's own ``starts_at`` guess *before*
            plausibility validation runs — asking the LLM to resolve a
            year-less date itself is unreliable (confirmed live for the
            now-retired ``lechampo.py``, see
            ``docs/decisions/0012-retire-lechampo.md``), so a known-true
            value should never be second-guessed by validating the LLM's
            own guess instead. ``None`` (the default) leaves every other
            scraper's behavior unchanged.

    Returns:
        The structured :class:`Sighting`, or None when extraction failed or
        was rejected.
    """
    try:
        extracted = await extractor.extract(listing.raw_text)
    except Exception:
        logger.exception(
            "llm extraction failed",
            extra={"ctx": {"source": listing.source.value, "url": listing.source_url}},
        )
        return None
    if known_starts_at is not None:
        extracted = extracted.model_copy(update={"starts_at": known_starts_at})
    issues = find_extraction_issues(extracted, now=datetime.now(UTC))
    if issues:
        logger.warning(
            "extraction rejected",
            extra={
                "ctx": {
                    "source": listing.source.value,
                    "url": listing.source_url,
                    "issues": issues,
                }
            },
        )
        return None
    return Sighting(
        extracted=extracted,
        source=listing.source,
        source_url=listing.source_url,
        booking_url=listing.booking_url,
    )


async def gather_events(
    items: Sequence[_Item],
    worker: Callable[[_Item], Awaitable[Sighting | None]],
    *,
    reporter: ProgressReporter,
    source: str,
) -> list[Sighting]:
    """Run ``worker`` over ``items`` concurrently, reporting progress per item.

    The total is reported up front (so the bar is determinate from the start),
    each completed item advances it, and ``None`` results (failed extractions)
    are dropped. Concurrency is bounded by :data:`_CONCURRENCY`.

    Args:
        items: The per-item inputs (URLs, listings, ...).
        worker: Async function turning one item into a sighting or ``None``.
        reporter: Progress reporter to drive the live display.
        source: The source value, for the reporter.

    Returns:
        The successfully produced sightings, order not guaranteed.
    """
    reporter.events_fetched(source, len(items))
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def run(item: _Item) -> Sighting | None:
        async with semaphore:
            sighting = await worker(item)
        reporter.event_processed(source)
        return sighting

    results = await asyncio.gather(*(run(item) for item in items))
    return [sighting for sighting in results if sighting is not None]


@runtime_checkable
class SourceScraper(Protocol):
    """A source able to yield screening sightings ready for ingestion."""

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        ...

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[Sighting]:
        """Fetch the source and return its screenings ready for ingestion.

        Implementations report progress via ``reporter`` during their slow work
        (``events_fetched`` once the total is known, then ``event_processed``
        per item) so the live bar is determinate.

        Args:
            client: Shared async HTTP client used for every request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`Sighting` per announced screening.
        """
        ...


@runtime_checkable
class VenuePassSource(Protocol):
    """A source able to report which subscription passes a venue accepts.

    Optional capability, not part of :class:`SourceScraper` — today only
    Paris Ciné Info carries this data (see
    ``io/scrapers/paris_cine_info.py::ParisCineInfoScraper.fetch_venue_passes``).
    The pipeline detects it via ``isinstance`` against this
    ``runtime_checkable`` Protocol (see
    ``pipeline.py::IngestionPipeline._apply_venue_passes``) rather than
    special-casing one source, so another source can implement it later with
    no pipeline change.
    """

    async def fetch_venue_passes(
        self, client: httpx.AsyncClient
    ) -> dict[str, list[str]]:
        """Return every known venue's accepted subscription-card codes.

        Args:
            client: Shared async HTTP client used for every request.

        Returns:
            A mapping of venue display name to its accepted card codes (see
            ``core/venues.py``'s ``pass_label``); a venue with no known
            accepted pass is omitted rather than mapped to an empty list.
        """
        ...


@dataclass(frozen=True, slots=True)
class VenueDetail:
    """Cinema/room-level facts about one venue, beyond its name and kind.

    Attributes:
        address: Street address, when known.
        website: Official website URL, when known.
        seat_count: Number of seats in the room, when known and unambiguous
            (see :class:`VenueDetailSource`).
        screen_width_m: Screen width in metres, same caveat as ``seat_count``.
        screen_height_m: Screen height in metres, same caveat as ``seat_count``.
    """

    address: str | None = None
    website: str | None = None
    seat_count: int | None = None
    screen_width_m: float | None = None
    screen_height_m: float | None = None


@runtime_checkable
class VenueDetailSource(Protocol):
    """A source able to report room-level detail about a venue.

    Optional capability, not part of :class:`SourceScraper` — today only
    Paris Ciné Info carries this data (see
    ``io/scrapers/paris_cine_info.py::ParisCineInfoScraper.fetch_venue_details``).
    Detected the same way as :class:`VenuePassSource`: via ``isinstance``
    against this ``runtime_checkable`` Protocol (see
    ``pipeline.py::IngestionPipeline._apply_venue_details``).
    """

    async def fetch_venue_details(
        self, client: httpx.AsyncClient
    ) -> dict[str, VenueDetail]:
        """Return every known venue's address/website/room detail.

        Args:
            client: Shared async HTTP client used for every request.

        Returns:
            A mapping of venue display name to its :class:`VenueDetail`; a
            venue with nothing known is omitted rather than mapped to an
            empty/default instance.
        """
        ...


@dataclass(frozen=True, slots=True)
class RatingRecord:
    """One external site's rating of a film, persisted as a :class:`FilmRating`.

    Attributes:
        source: External site this rating was sourced from.
        rating: The rating value, on that source's own native scale.
        url: Direct link to the film on that source, when known.
    """

    source: RatingSource
    rating: float
    url: str | None = None


@runtime_checkable
class FilmRatingSource(Protocol):
    """A source able to report per-film ratings from external rating sites.

    Optional capability, not part of :class:`SourceScraper` — today only
    Paris Ciné Info carries this data (see
    ``io/scrapers/paris_cine_info.py::ParisCineInfoScraper.fetch_film_ratings``
    and ``docs/decisions/0013-film-ratings-from-paris-cine-info.md``).
    Detected the same way as :class:`VenuePassSource`/:class:`VenueDetailSource`:
    via ``isinstance`` against this ``runtime_checkable`` Protocol (see
    ``pipeline.py::IngestionPipeline._apply_film_ratings``).
    """

    async def fetch_film_ratings(
        self, client: httpx.AsyncClient
    ) -> dict[str, list[RatingRecord]]:
        """Return every known film's ratings, keyed by IMDb id.

        Args:
            client: Shared async HTTP client used for every request.

        Returns:
            A mapping of IMDb id (``"tt"`` followed by digits, matching
            ``Film.imdb_id``'s format) to that film's :class:`RatingRecord`
            list; a film with no rating known from any source is omitted.
        """
        ...


def scan_balanced(
    text: str, start: int, *, open_char: str, close_char: str
) -> str | None:
    """Return the balanced-delimiter substring starting at ``text[start]``.

    Tracks string state so a delimiter inside a JSON string value does not
    affect the depth count. Pulls one JSON value out of a larger embedded-JS
    blob rather than parsing the whole page as JSON — see
    ``io/scrapers/paris_cine_info.py``'s ``parse_venue_passes`` (``[``/``]``,
    the ``const cineOptions = [...]`` catalogue array).

    Args:
        text: The text to scan.
        start: Index of the opening delimiter (``text[start] == open_char``).
        open_char: The opening delimiter, e.g. ``"{"`` or ``"["``.
        close_char: The matching closing delimiter, e.g. ``"}"`` or ``"]"``.

    Returns:
        The balanced substring (including both delimiters), or ``None`` when
        the text ends before the depth returns to zero.
    """
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif not in_string:
            if char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
    return None
