"""TMDB enrichment: match a film and fill its metadata.

A thin async client over TMDB's v3 API — a title search picks the film, then
one details call (with ``append_to_response=credits``) fetches everything the
:class:`Film` row persists: original title, director, release year, runtime,
genres, synopsis, poster, and rating. Enrichment runs once per film row —
every screening of the film shares it. Network failures are the caller's
concern; the client only maps successful responses.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from cine_event_bot.core.models import Film

_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
_DETAILS_URL = "https://api.themoviedb.org/3/movie/{tmdb_id}"
_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"
_DIRECTOR_JOB = "Director"


@dataclass(frozen=True, slots=True)
class MovieMatch:
    """A film matched on TMDB, reduced to the fields we persist.

    Attributes:
        tmdb_id: TMDB identifier of the matched film.
        original_title: Original-language title, when TMDB provides one.
        director: Director name(s), comma-joined when there are several.
        release_year: Release year, when a release date is known.
        runtime_minutes: Runtime in minutes, when known.
        genres: Genre names, when TMDB lists any.
        overview: Synopsis, when TMDB provides one.
        poster_url: Absolute poster URL, when a poster exists.
        vote_average: TMDB rating (0-10), when rated.
    """

    tmdb_id: int
    original_title: str | None
    director: str | None
    release_year: int | None
    runtime_minutes: int | None
    genres: tuple[str, ...] | None
    overview: str | None
    poster_url: str | None
    vote_average: float | None


class TmdbClient:
    """Async client over the TMDB v3 search and movie-details endpoints."""

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        """Bind the client to an HTTP client and a TMDB API key.

        Args:
            client: Shared async HTTP client used for requests.
            api_key: TMDB v3 API key.
        """
        self._client = client
        self._api_key = api_key

    async def find(self, title: str) -> MovieMatch | None:
        """Match a film by title and return its full metadata.

        Two requests: a title search to pick the best match, then the movie
        details (credits appended) for the fields the search response lacks
        (director, runtime, genres, rating).

        Args:
            title: Film title to search for.

        Returns:
            The matched film's metadata, or None when TMDB has no result.
        """
        tmdb_id = await self._search_first_id(title)
        if tmdb_id is None:
            return None
        return _to_match(await self._details(tmdb_id))

    async def _search_first_id(self, title: str) -> int | None:
        """Return the TMDB id of the best title match, or None."""
        params = {
            "api_key": self._api_key,
            "query": title,
            "language": "fr-FR",
            "include_adult": "false",
        }
        response = await self._client.get(_SEARCH_URL, params=params)
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return None
        return int(results[0]["id"])

    async def _details(self, tmdb_id: int) -> dict[str, Any]:
        """Fetch a movie's details with its credits appended."""
        params = {
            "api_key": self._api_key,
            "language": "fr-FR",
            "append_to_response": "credits",
        }
        response = await self._client.get(
            _DETAILS_URL.format(tmdb_id=tmdb_id), params=params
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload


class TmdbEnricher:
    """Fills a film's TMDB fields from the best title match."""

    def __init__(self, client: TmdbClient) -> None:
        """Bind the enricher to a TMDB client.

        Args:
            client: TMDB client used to look films up.
        """
        self._client = client

    async def enrich(self, film: Film) -> None:
        """Enrich a film in place with TMDB metadata, if a match is found.

        Leaves the film untouched when no result matches its title.

        Args:
            film: The film to enrich; mutated in place.
        """
        match = await self._client.find(film.title)
        if match is None:
            return
        film.tmdb_id = match.tmdb_id
        film.original_title = match.original_title
        film.director = match.director
        film.release_year = match.release_year
        film.runtime_minutes = match.runtime_minutes
        film.genres = list(match.genres) if match.genres is not None else None
        film.overview = match.overview
        film.poster_url = match.poster_url
        film.vote_average = match.vote_average


def build_tmdb_enricher(client: httpx.AsyncClient, api_key: str) -> TmdbEnricher:
    """Build a TMDB enricher over a shared HTTP client.

    Args:
        client: Shared async HTTP client used for requests.
        api_key: TMDB v3 API key.

    Returns:
        A :class:`TmdbEnricher` ready to enrich films.
    """
    return TmdbEnricher(TmdbClient(client, api_key))


def _to_match(details: dict[str, Any]) -> MovieMatch:
    """Map a movie-details payload (credits appended) to a :class:`MovieMatch`."""
    poster_path = details.get("poster_path")
    return MovieMatch(
        tmdb_id=int(details["id"]),
        original_title=_non_empty_str(details.get("original_title")),
        director=_director(details),
        release_year=_release_year(details.get("release_date")),
        runtime_minutes=_positive_int(details.get("runtime")),
        genres=_genres(details.get("genres")),
        overview=_non_empty_str(details.get("overview")),
        poster_url=f"{_IMAGE_BASE_URL}{poster_path}"
        if isinstance(poster_path, str)
        else None,
        vote_average=_rating(details.get("vote_average")),
    )


def _director(details: dict[str, Any]) -> str | None:
    """Comma-join the crew members credited as director, if any."""
    crew = (details.get("credits") or {}).get("crew") or []
    names = [
        member["name"]
        for member in crew
        if isinstance(member, dict)
        and member.get("job") == _DIRECTOR_JOB
        and isinstance(member.get("name"), str)
    ]
    return ", ".join(names) if names else None


def _genres(genres: object) -> tuple[str, ...] | None:
    """Extract genre names from TMDB's ``[{"id":..,"name":..}]`` list."""
    if not isinstance(genres, list):
        return None
    names = tuple(
        genre["name"]
        for genre in genres
        if isinstance(genre, dict) and isinstance(genre.get("name"), str)
    )
    return names or None


def _non_empty_str(value: object) -> str | None:
    """Return the value when it is a non-empty string, else None."""
    return value if isinstance(value, str) and value else None


def _positive_int(value: object) -> int | None:
    """Return the value as an int when it is a positive number, else None."""
    if isinstance(value, int) and value > 0:
        return value
    return None


def _rating(value: object) -> float | None:
    """Return a non-zero TMDB rating as a float, else None."""
    if isinstance(value, int | float) and value > 0:
        return float(value)
    return None


def _release_year(release_date: object) -> int | None:
    """Extract the year from a TMDB ``YYYY-MM-DD`` release date, if valid."""
    if not isinstance(release_date, str) or len(release_date) < 4:
        return None
    try:
        return int(release_date[:4])
    except ValueError:
        return None
