"""Scraper for Paris Ciné Info (paris-cine.info), an authenticated aggregator.

Unlike every other source, this one is a genuine JSON API behind a login: an
account is required (see ``docs/setup.md``). Two endpoints cover the
discovery step with no LLM needed (Level 2) — ``get_movies.php?events=true``
lists every film the site currently flags as a special screening, and
``get_showtimes.php`` lists that film's showtimes across dozens of partner
Paris cinemas at once, each with an exact time and a direct booking link.

What still needs the LLM is each showtime's free-text ``com`` field, which
reads exactly like the announcement text scraped from single-venue sources
(e.g. "Avant-première Festival des Cinémas Indépendants Parisiens, projection
présentée par..."). A showtime with no comment is an ordinary screening —
even for a film flagged as "événement" — and is not ingested.
"""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from cine_event_bot.core.models import Sighting, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.io.llm import EventExtractor
from cine_event_bot.io.scrapers.base import RawListing, gather_events, structure_via_llm

_SITE_URL = "https://paris-cine.info/"
_LOGIN_URL = f"{_SITE_URL}login/do_login.php"
_MOVIES_URL = f"{_SITE_URL}get_movies.php"
_SHOWTIMES_URL = f"{_SITE_URL}get_showtimes.php"
_LOGIN_SUCCESS = "login_success"

# The API's showtime timestamps are naive local time, not UTC.
_PARIS = ZoneInfo("Europe/Paris")


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

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        return Source.PARIS_CINE_INFO

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[Sighting]:
        """Log in, fetch every flagged film's showtimes, and structure the notable ones.

        Discovery (login, the event-flagged film list, and each film's
        showtimes) runs first and is not itself progress-reported, matching
        every other scraper's convention of a determinate bar only once the
        real total is known — here, once the candidate showtimes (those
        carrying a comment) are identified.

        Args:
            client: Shared async HTTP client used for every request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`Sighting` per successfully classified special showtime.
        """
        await self._log_in(client)
        movies = await self._fetch_event_movies(client)
        listings: list[RawListing] = []
        for movie in movies:
            showtimes = await self._fetch_showtimes(client, movie)
            listings.extend(
                listing
                for showtime in showtimes
                if (listing := build_listing(movie, showtime)) is not None
            )

        async def extract(listing: RawListing) -> Sighting | None:
            return await structure_via_llm(self._extractor, listing)

        return await gather_events(
            listings, extract, reporter=reporter, source=self.source.value
        )

    async def _log_in(self, client: httpx.AsyncClient) -> None:
        """Authenticate the shared client, raising if the credentials are rejected."""
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

    async def _fetch_event_movies(
        self, client: httpx.AsyncClient
    ) -> list[dict[str, Any]]:
        """Return every film currently flagged as a special screening."""
        response = await client.get(_MOVIES_URL, params={"events": "true"})
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


def build_listing(movie: dict[str, Any], showtime: dict[str, Any]) -> RawListing | None:
    """Build a raw listing from one showtime, or None if it is not a special one.

    A showtime with a blank ``com`` is an ordinary screening — even for a film
    the site otherwise flags as "événement" — and is not a candidate for the
    digest; only a non-empty comment carries the "why this matters" context an
    :class:`~cine_event_bot.io.llm.EventExtractor` needs.

    Args:
        movie: One entry from ``get_movies.php``'s ``data`` array.
        showtime: One entry from ``get_showtimes.php``'s ``showtimes`` array.

    Returns:
        The raw listing ready for LLM structuring, or None when the showtime
        has no comment or is missing an essential field.
    """
    comment = showtime.get("com")
    if not isinstance(comment, str) or not comment.strip():
        return None
    title = movie.get("ti")
    venue = showtime.get("title")
    starts_at = _parse_datetime(showtime.get("start"))
    if not isinstance(title, str) or not isinstance(venue, str) or starts_at is None:
        return None
    raw_text = f"{venue}\n{title}\nDate : {starts_at.isoformat()}\n{comment}"
    booking_url = showtime.get("book") or None
    return RawListing(
        source=Source.PARIS_CINE_INFO,
        source_url=booking_url or _SITE_URL,
        raw_text=raw_text,
        booking_url=booking_url,
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
