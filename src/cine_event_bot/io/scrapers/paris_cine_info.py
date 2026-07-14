"""Scraper for Paris Ciné Info (paris-cine.info), an authenticated aggregator.

Unlike every other source, this one is a genuine JSON API behind a login: an
account is required (see ``docs/setup.md``). Two endpoints cover the
discovery step with no LLM needed (Level 2) — ``get_movies.php`` (the full
catalogue, not just the site's own "événement" flag — see
``docs/decisions/0008-drop-allocine-width-source.md``) lists every film
currently showing, and ``get_showtimes.php`` lists that film's showtimes
across dozens of partner Paris cinemas at once, each with an exact time and a
direct booking link.

Every showtime becomes a sighting: title, venue, and start time are always
known with certainty from the structured API. A showtime's free-text ``com``
field, when present, reads exactly like the announcement text scraped from
single-venue sources (e.g. "Avant-première Festival des Cinémas Indépendants
Parisiens, projection présentée par...") and is worth classifying via the LLM
(``event_type``, ``has_team_present``, ``cycle_name``, ``description``); the
large majority of showtimes carry no comment at all and are stored directly
as ordinary screenings (``event_type=None``) with no LLM call — see
``docs/coverage-matrix.md`` (only ~0.3% of showtimes carry a comment).

When a comment is present, its title/venue/start-time guesses are discarded
rather than trusted: an early version fed venue+title+comment to the LLM as
one blob and kept its full output, which occasionally merged two lines into
one garbled title (e.g. "Les 7 Parnassiens Rita et Crocodile") and fell back
to a placeholder venue ("null", "No venue specified") instead of the venue
already known for certain.

The authenticated homepage (``GET /``) separately embeds a
``const cineOptions = [...]`` JS array — a department-grouped directory of
every partner cinema, each carrying the subscription cards it accepts
(``ugc``, ``pass``, ``librepass``... — see ``core/venues.py``'s
``pass_label``). This is unrelated to the showtimes API and is fetched
on-demand by :meth:`ParisCineInfoScraper.fetch_venue_passes`, not on every
run of :meth:`fetch_events` — the pipeline calls it once, separately (see
``pipeline.py::IngestionPipeline._apply_venue_passes``).

A third, per-room endpoint (``get_pcitheatre.php``, given a
``theatre_id``/``screen_id`` pair, confirmed live) returns a room's address,
official site URL, seat count, and screen width/height in metres. Every
showtime from ``get_showtimes.php``
already carries its ``tid``/``screen_id`` (confirmed live:
``{'title': 'Le Louxor', 'tid': 'W7510', 'screen_id': 15750, ...}``), so
:meth:`fetch_events` records them per venue name as a free side effect of
its normal walk; :meth:`fetch_venue_details` then fetches each venue's
detail once, using whatever :meth:`fetch_events` most recently observed.

Every :class:`~cine_event_bot.core.models.Sighting` this scraper produces
also carries the showtime's ``tid`` as ``venue_external_id`` — a stable
identifier ``EventRepository._resolve_venue`` uses to recognize the same
physical venue across sources that describe it with different wording
(see ``docs/decisions/0012-retire-lechampo.md``'s venue-name fragmentation
follow-up), the same role TMDB's id already plays for films.
"""

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from cine_event_bot.core.models import ExtractedEvent, Sighting, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import (
    RawListing,
    VenueDetail,
    gather_events,
    scan_balanced,
    structure_via_llm,
)


@dataclass(frozen=True, slots=True)
class _KnownFacts:
    """The always-certain facts of one showtime, read from the structured API."""

    title: str
    venue: str
    starts_at: datetime
    comment: str | None
    booking_url: str | None
    venue_external_id: str | None


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A commented showtime ready for LLM classification, carrying its known-true facts.

    ``listing`` still includes the venue/title as text context so the LLM can
    correctly interpret the comment (e.g. whose team is presenting), but its
    title/venue/start-time output is discarded in favor of these fields.
    """

    listing: RawListing
    known_title: str
    known_venue: str
    known_starts_at: datetime
    known_venue_external_id: str | None


_SITE_URL = "https://paris-cine.info/"
_LOGIN_URL = f"{_SITE_URL}login/do_login.php"
_MOVIES_URL = f"{_SITE_URL}get_movies.php"
_SHOWTIMES_URL = f"{_SITE_URL}get_showtimes.php"
_THEATRE_URL = f"{_SITE_URL}get_pcitheatre.php"
_LOGIN_SUCCESS = "login_success"

# The API's showtime timestamps are naive local time, not UTC.
_PARIS = ZoneInfo("Europe/Paris")

# Stable anchor for the venue-passes catalogue embedded in the homepage's
# inline <script> — a hand-named JS variable, not a generated identifier.
_CINE_OPTIONS_MARKER = "const cineOptions = "

# Bounded concurrency for the per-venue get_pcitheatre.php calls in
# fetch_venue_details, matching every other scraper's per-item bound.
_CONCURRENCY = 5


class ParisCineInfoScraper:
    """Scrapes special screenings from the authenticated paris-cine.info API."""

    def __init__(self, extractor: EventExtractor, login: str, password: str) -> None:
        """Bind the scraper to its LLM extractor and account credentials.

        Args:
            extractor: LLM-backed extractor structuring each showtime's
                free-text comment.
            login: paris-cine.info account email.
            password: paris-cine.info account password.
        """
        self._extractor = extractor
        self._login = login
        self._password = password
        self._logged_in = False
        # Populated by fetch_events as a free side effect of its showtime
        # walk; consumed by fetch_venue_details. A venue name maps to every
        # distinct (theatre_id, screen_id) pair seen this run — more than
        # one means the venue has multiple rooms, so its room-specific
        # detail (seat count, screen size) is ambiguous and skipped (see
        # fetch_venue_details).
        self._theatre_screens: dict[str, set[tuple[str, int]]] = defaultdict(set)

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        return Source.PARIS_CINE_INFO

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[Sighting]:
        """Log in, fetch every film's showtimes, and structure the commented ones.

        Discovery (login, the full film catalogue, and each film's showtimes)
        runs first and is not itself progress-reported, matching every other
        scraper's convention of a determinate bar only once the real total is
        known — here, once every showtime is enumerated. Each film's
        showtimes are fetched with the same bounded concurrency as every
        other per-item operation in this class (see :meth:`fetch_venue_details`)
        rather than one at a time — a real, measured cost at the catalogue's
        real size (see ``docs/performance-audit.md``'s Finding 3). An
        uncommented showtime needs no LLM call and is turned into an ordinary
        sighting directly; only a commented one goes through
        :func:`gather_events`'s bounded-concurrency LLM classification.

        Args:
            client: Shared async HTTP client used for every request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`Sighting` per showtime — classified when it carried a
            comment, ordinary otherwise, dropped only when a classification
            was attempted and rejected.
        """
        await self._log_in(client)
        movies = await self._fetch_movies(client)
        semaphore = asyncio.Semaphore(_CONCURRENCY)

        async def fetch_one_movie(movie: dict[str, Any]) -> list[dict[str, Any]]:
            async with semaphore:
                return await self._fetch_showtimes(client, movie)

        # asyncio.gather preserves result order matching input order
        # regardless of completion order, so this stays equivalent to a
        # plain sequential loop below — only the fetch itself is concurrent.
        showtimes_per_movie = await asyncio.gather(
            *(fetch_one_movie(movie) for movie in movies)
        )
        items: list[_Candidate | Sighting] = []
        for movie, showtimes in zip(movies, showtimes_per_movie, strict=True):
            for showtime in showtimes:
                self._record_theatre_screen(showtime)
                item = build_showtime_item(movie, showtime)
                if item is not None:
                    items.append(item)

        async def extract(item: _Candidate | Sighting) -> Sighting | None:
            if isinstance(item, Sighting):
                return item
            return await _classify(self._extractor, item)

        return await gather_events(
            items, extract, reporter=reporter, source=self.source.value
        )

    def _record_theatre_screen(self, showtime: dict[str, Any]) -> None:
        """Record one showtime's (theatre id, screen id) under its venue name.

        A free side effect of the walk already happening in
        :meth:`fetch_events`, consumed later by :meth:`fetch_venue_details`
        — see this module's docstring.
        """
        venue = showtime.get("title")
        theatre_id = showtime.get("tid")
        screen_id = showtime.get("screen_id")
        if (
            isinstance(venue, str)
            and isinstance(theatre_id, str)
            and isinstance(screen_id, int)
        ):
            self._theatre_screens[venue].add((theatre_id, screen_id))

    async def _log_in(self, client: httpx.AsyncClient) -> None:
        """Authenticate the shared client once, raising if credentials are rejected.

        Idempotent per scraper instance: :meth:`fetch_events` and
        :meth:`fetch_venue_passes` both need an authenticated client and can
        run in either order within one pipeline run (see
        ``pipeline.py::IngestionPipeline._apply_venue_passes``) — confirmed
        live, a *second* login attempt on an already-authenticated client is
        rejected by the site (its session cookie already carries an active
        login, and re-posting credentials against it does not return
        ``login_success`` again). Logging in only once per instance sidesteps
        that rather than depending on caller ordering to avoid it.
        """
        if self._logged_in:
            return
        response = await client.post(
            _LOGIN_URL,
            data={
                "email": self._login,
                "password": self._password,
                "remember": "true",
            },
        )
        response.raise_for_status()
        if response.text.strip() != _LOGIN_SUCCESS:
            raise RuntimeError(
                "paris-cine.info login rejected the configured credentials"
            )
        self._logged_in = True

    async def _fetch_movies(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """Return the full film catalogue currently showing (no "événement" filter)."""
        response = await client.get(_MOVIES_URL)
        response.raise_for_status()
        movies = response.json().get("data", [])
        return movies if isinstance(movies, list) else []

    async def _fetch_showtimes(
        self, client: httpx.AsyncClient, movie: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Return every showtime of one film across every partner cinema."""
        response = await client.get(
            _SHOWTIMES_URL,
            params={"mov_id": movie.get("id"), "mov_lang": movie.get("la", "")},
        )
        response.raise_for_status()
        showtimes = response.json().get("showtimes", [])
        return showtimes if isinstance(showtimes, list) else []

    async def fetch_venue_passes(
        self, client: httpx.AsyncClient
    ) -> dict[str, list[str]]:
        """Fetch and parse the accepted-subscription-card catalogue per venue.

        Implements :class:`~cine_event_bot.io.scrapers.base.VenuePassSource`.
        Separate from :meth:`fetch_events`: this data lives on the homepage,
        not the showtimes API, and only needs fetching once per run.

        Args:
            client: Shared async HTTP client used for every request.

        Returns:
            A mapping of venue display name to its accepted card codes (see
            :func:`parse_venue_passes`).
        """
        await self._log_in(client)
        response = await client.get(_SITE_URL)
        response.raise_for_status()
        return parse_venue_passes(response.text)

    async def fetch_venue_details(
        self, client: httpx.AsyncClient
    ) -> dict[str, VenueDetail]:
        """Fetch each known venue's address/website/room detail, one call each.

        Implements :class:`~cine_event_bot.io.scrapers.base.VenueDetailSource`.
        Uses the ``(theatre_id, screen_id)`` pairs :meth:`fetch_events`
        recorded per venue name during its own walk — this method fetches
        nothing new by discovery, only per-venue detail for venues already
        observed this run. Calling it before :meth:`fetch_events` has run
        yields an empty mapping, not an error.

        Args:
            client: Shared async HTTP client used for every request.

        Returns:
            A mapping of venue display name to its :class:`VenueDetail`.
        """
        await self._log_in(client)
        semaphore = asyncio.Semaphore(_CONCURRENCY)
        results: dict[str, VenueDetail] = {}

        async def fetch_one(venue_name: str, screens: set[tuple[str, int]]) -> None:
            theatre_id, screen_id = next(iter(screens))
            async with semaphore:
                detail = await self._fetch_theatre_detail(
                    client, theatre_id, screen_id, include_room=len(screens) == 1
                )
            if detail is not None:
                results[venue_name] = detail

        await asyncio.gather(
            *(
                fetch_one(venue_name, screens)
                for venue_name, screens in self._theatre_screens.items()
            )
        )
        return results

    async def _fetch_theatre_detail(
        self,
        client: httpx.AsyncClient,
        theatre_id: str,
        screen_id: int,
        *,
        include_room: bool,
    ) -> VenueDetail | None:
        """Fetch and parse one (theatre, screen) pair's detail, or None if absent."""
        response = await client.get(
            _THEATRE_URL,
            params={"theatre_id": theatre_id, "screen_id": screen_id},
        )
        response.raise_for_status()
        theaters = response.json().get("theater")
        if not isinstance(theaters, list) or not theaters:
            return None
        theater = theaters[0]
        if not isinstance(theater, dict):
            return None
        return _parse_venue_detail(theater, include_room=include_room)


def _parse_known_facts(
    movie: dict[str, Any], showtime: dict[str, Any]
) -> _KnownFacts | None:
    """Read one showtime's always-certain facts, or None if essential data is missing.

    Args:
        movie: One entry from ``get_movies.php``'s ``data`` array.
        showtime: One entry from ``get_showtimes.php``'s ``showtimes`` array.

    Returns:
        The known facts, or None when the title, venue, or start time is
        missing or unparseable.
    """
    title = movie.get("ti")
    venue = showtime.get("title")
    starts_at = _parse_datetime(showtime.get("start"))
    if not isinstance(title, str) or not isinstance(venue, str) or starts_at is None:
        return None
    raw_comment = showtime.get("com")
    comment = (
        raw_comment.strip()
        if isinstance(raw_comment, str) and raw_comment.strip()
        else None
    )
    booking_url = showtime.get("book") or None
    raw_tid = showtime.get("tid")
    venue_external_id = raw_tid if isinstance(raw_tid, str) and raw_tid else None
    return _KnownFacts(
        title=title,
        venue=venue,
        starts_at=starts_at,
        comment=comment,
        booking_url=booking_url,
        venue_external_id=venue_external_id,
    )


def build_showtime_item(
    movie: dict[str, Any], showtime: dict[str, Any]
) -> _Candidate | Sighting | None:
    """Build the work item for one showtime, or None if an essential field is missing.

    A commented showtime carries "why this matters" context an
    :class:`~cine_event_bot.io.llm.EventExtractor` can classify, and becomes a
    :class:`_Candidate`. An uncommented showtime is an ordinary screening —
    its facts are already fully known from the structured API, so it becomes
    a :class:`Sighting` directly, with no LLM call.

    Args:
        movie: One entry from ``get_movies.php``'s ``data`` array.
        showtime: One entry from ``get_showtimes.php``'s ``showtimes`` array.

    Returns:
        A candidate for LLM classification, an already-built ordinary
        sighting, or None when the showtime is missing an essential field.
    """
    facts = _parse_known_facts(movie, showtime)
    if facts is None:
        return None
    source_url = facts.booking_url or _SITE_URL
    if facts.comment is None:
        return Sighting(
            extracted=ExtractedEvent(
                title=facts.title, venue=facts.venue, starts_at=facts.starts_at
            ),
            source=Source.PARIS_CINE_INFO,
            source_url=source_url,
            booking_url=facts.booking_url,
            venue_external_id=facts.venue_external_id,
        )
    raw_text = (
        f"{facts.venue}\n{facts.title}\nDate : {facts.starts_at.isoformat()}\n"
        f"{facts.comment}"
    )
    listing = RawListing(
        source=Source.PARIS_CINE_INFO,
        source_url=source_url,
        raw_text=raw_text,
        booking_url=facts.booking_url,
    )
    return _Candidate(
        listing=listing,
        known_title=facts.title,
        known_venue=facts.venue,
        known_starts_at=facts.starts_at,
        known_venue_external_id=facts.venue_external_id,
    )


async def _classify(
    extractor: EventExtractor, candidate: _Candidate
) -> Sighting | None:
    """Classify a candidate's comment via the LLM, keeping only its known facts.

    The LLM's own title/venue/start-time guesses are discarded — they are
    already known with certainty from the structured API — and only its
    classification of the comment (event type, team presence, cycle, free-text
    description) is kept.

    Args:
        extractor: LLM-backed extractor classifying the comment.
        candidate: The showtime to classify, with its known-true facts.

    Returns:
        The sighting with known facts restored, or None when classification
        failed or was rejected.
    """
    sighting = await structure_via_llm(extractor, candidate.listing)
    if sighting is None:
        return None
    corrected = sighting.extracted.model_copy(
        update={
            "title": candidate.known_title,
            "venue": candidate.known_venue,
            "starts_at": candidate.known_starts_at,
        }
    )
    return Sighting(
        extracted=corrected,
        source=sighting.source,
        source_url=sighting.source_url,
        booking_url=sighting.booking_url,
        venue_external_id=candidate.known_venue_external_id,
    )


def _parse_datetime(value: object) -> datetime | None:
    """Parse the API's naive Paris-local timestamp into a UTC datetime."""
    if not isinstance(value, str):
        return None
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        return None
    return naive.replace(tzinfo=_PARIS).astimezone(UTC)


def parse_venue_passes(html: str) -> dict[str, list[str]]:
    """Parse the homepage's ``cineOptions`` catalogue into venue -> card codes.

    Anchors on the ``const cineOptions = `` marker (a stable, hand-named JS
    variable) rather than a generated class or position, then scans the
    following bracket-balanced array and parses it as JSON — the array is
    department-grouped (``[{"options": [{"label", "cards": [...]}]}]``), so
    every department's cinemas are flattened into one flat mapping.

    Args:
        html: HTML of the authenticated paris-cine.info homepage (``GET /``).

    Returns:
        A mapping of venue display name to its accepted card codes; a venue
        with no accepted card, a malformed catalogue, or a missing marker
        yields an empty mapping rather than raising.
    """
    marker_index = html.find(_CINE_OPTIONS_MARKER)
    if marker_index == -1:
        return {}
    array_start = html.find("[", marker_index)
    if array_start == -1:
        return {}
    array_text = scan_balanced(html, array_start, open_char="[", close_char="]")
    if array_text is None:
        return {}
    try:
        departments = json.loads(array_text)
    except json.JSONDecodeError:
        return {}
    return dict(_flatten_venue_passes(departments))


def _flatten_venue_passes(departments: object) -> list[tuple[str, list[str]]]:
    """Flatten the department-grouped catalogue into (venue name, cards) pairs."""
    if not isinstance(departments, list):
        return []
    return [
        pair
        for department in departments
        if isinstance(department, dict)
        for pair in _department_venue_passes(department)
    ]


def _department_venue_passes(
    department: dict[str, Any],
) -> list[tuple[str, list[str]]]:
    """Return one department's (venue name, cards) pairs, skipping empty ones."""
    pairs: list[tuple[str, list[str]]] = []
    for cinema in department.get("options") or []:
        if not isinstance(cinema, dict):
            continue
        name = cinema.get("label")
        cards = _card_codes(cinema.get("cards"))
        if isinstance(name, str) and cards:
            pairs.append((name, cards))
    return pairs


def _card_codes(cards: object) -> list[str]:
    """Extract the ``cardname`` of every well-formed card entry."""
    if not isinstance(cards, list):
        return []
    codes = []
    for card in cards:
        if isinstance(card, dict):
            code = card.get("cardname")
            if isinstance(code, str):
                codes.append(code)
    return codes


def _parse_venue_detail(theater: dict[str, Any], *, include_room: bool) -> VenueDetail:
    """Map one ``get_pcitheatre.php`` theater entry to a :class:`VenueDetail`.

    ``address``/``website`` are cinema-level and always mapped when present.
    ``seat_count``/``screen_width_m``/``screen_height_m`` are room-level —
    only mapped when ``include_room`` is true, i.e. the caller confirmed
    this venue had exactly one distinct room this run (see
    :meth:`ParisCineInfoScraper.fetch_venue_details`).

    Args:
        theater: One entry from the endpoint's ``theater`` array.
        include_room: Whether to map the room-specific fields.

    Returns:
        The parsed detail; any individually unparseable field is left
        ``None`` rather than raising.
    """
    seat_count = _to_int(theater.get("seatCount")) if include_room else None
    screen_width_m = _to_float(theater.get("screenWidth")) if include_room else None
    screen_height_m = _to_float(theater.get("screenHeight")) if include_room else None
    return VenueDetail(
        address=_non_empty_str(theater.get("addr")),
        website=_non_empty_str(theater.get("url")),
        seat_count=seat_count,
        screen_width_m=screen_width_m,
        screen_height_m=screen_height_m,
    )


def _non_empty_str(value: object) -> str | None:
    """Return the value when it is a non-empty string, else None."""
    return value if isinstance(value, str) and value else None


def _to_int(value: object) -> int | None:
    """Parse a value (the API sends numbers as strings) as a positive int."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _to_float(value: object) -> float | None:
    """Parse a value (the API sends numbers as strings) as a positive float."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
