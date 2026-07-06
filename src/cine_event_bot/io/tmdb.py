"""TMDB enrichment: match a film and fill its metadata.

A thin async client over TMDB's v3 search endpoint, plus an enricher that fills
a :class:`Film`'s TMDB fields (id, overview, poster, release year) from the
best title match. Enrichment runs once per film row — every screening of the
film shares it. Network failures are the caller's concern; the enricher only
maps a successful response.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from cine_event_bot.core.models import Film

_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"


@dataclass(frozen=True, slots=True)
class MovieMatch:
    """A film matched on TMDB, reduced to the fields we persist.

    Attributes:
        tmdb_id: TMDB identifier of the matched film.
        overview: Synopsis, when TMDB provides one.
        poster_url: Absolute poster URL, when a poster exists.
        release_year: Release year, when a release date is known.
    """

    tmdb_id: int
    overview: str | None
    poster_url: str | None
    release_year: int | None


class TmdbClient:
    """Async client over the TMDB v3 movie-search endpoint."""

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        """Bind the client to an HTTP client and a TMDB API key.

        Args:
            client: Shared async HTTP client used for requests.
            api_key: TMDB v3 API key.
        """
        self._client = client
        self._api_key = api_key

    async def search(self, title: str) -> MovieMatch | None:
        """Search TMDB for a film by title and return the best match.

        Args:
            title: Film title to search for.

        Returns:
            The first result mapped to a :class:`MovieMatch`, or None when TMDB
            returns no result.
        """
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
        return _to_match(results[0])


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
        match = await self._client.search(film.title)
        if match is None:
            return
        film.tmdb_id = match.tmdb_id
        film.overview = match.overview
        film.poster_url = match.poster_url
        film.release_year = match.release_year


def build_tmdb_enricher(client: httpx.AsyncClient, api_key: str) -> TmdbEnricher:
    """Build a TMDB enricher over a shared HTTP client.

    Args:
        client: Shared async HTTP client used for requests.
        api_key: TMDB v3 API key.

    Returns:
        A :class:`TmdbEnricher` ready to enrich films.
    """
    return TmdbEnricher(TmdbClient(client, api_key))


def _to_match(result: dict[str, Any]) -> MovieMatch:
    """Map one TMDB search result to a :class:`MovieMatch`."""
    poster_path = result.get("poster_path")
    overview = result.get("overview")
    return MovieMatch(
        tmdb_id=int(result["id"]),
        overview=overview if isinstance(overview, str) and overview else None,
        poster_url=f"{_IMAGE_BASE_URL}{poster_path}"
        if isinstance(poster_path, str)
        else None,
        release_year=_release_year(result.get("release_date")),
    )


def _release_year(release_date: object) -> int | None:
    """Extract the year from a TMDB ``YYYY-MM-DD`` release date, if valid."""
    if not isinstance(release_date, str) or len(release_date) < 4:
        return None
    try:
        return int(release_date[:4])
    except ValueError:
        return None
