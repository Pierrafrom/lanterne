"""Tests for the TMDB client and the film enricher (httpx mocked)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from cine_event_bot.core.models import Film
from cine_event_bot.io.tmdb import TmdbClient, TmdbEnricher


def _client_returning(payload: dict[str, Any]) -> tuple[TmdbClient, MagicMock]:
    response = MagicMock()
    response.json = MagicMock(return_value=payload)
    response.raise_for_status = MagicMock()
    http = MagicMock()
    http.get = AsyncMock(return_value=response)
    return TmdbClient(http, api_key="key"), http


def _result(**overrides: Any) -> dict[str, Any]:
    result = {
        "id": 693134,
        "title": "Dune: Part Two",
        "overview": "Paul Atreides unites with the Fremen.",
        "poster_path": "/poster.jpg",
        "release_date": "2024-02-27",
    }
    result.update(overrides)
    return result


def _film() -> Film:
    return Film(title_key="dune: part two", title="Dune: Part Two")


async def test_search_maps_first_result() -> None:
    client, _ = _client_returning({"results": [_result()]})

    match = await client.search("Dune: Part Two")

    assert match is not None
    assert match.tmdb_id == 693134
    assert match.overview == "Paul Atreides unites with the Fremen."
    assert match.poster_url == "https://image.tmdb.org/t/p/w500/poster.jpg"
    assert match.release_year == 2024


async def test_search_passes_title_as_query() -> None:
    client, http = _client_returning({"results": [_result()]})

    await client.search("Mulholland Drive")

    _, kwargs = http.get.call_args
    assert kwargs["params"]["query"] == "Mulholland Drive"
    assert kwargs["params"]["api_key"] == "key"


async def test_search_returns_none_when_no_results() -> None:
    client, _ = _client_returning({"results": []})

    assert await client.search("Unknown film") is None


async def test_search_handles_missing_poster_and_date() -> None:
    client, _ = _client_returning(
        {"results": [_result(poster_path=None, release_date="")]}
    )

    match = await client.search("Dune")

    assert match is not None
    assert match.poster_url is None
    assert match.release_year is None


async def test_enricher_fills_film_fields() -> None:
    client, _ = _client_returning({"results": [_result()]})
    film = _film()

    await TmdbEnricher(client).enrich(film)

    assert film.tmdb_id == 693134
    assert film.overview == "Paul Atreides unites with the Fremen."
    assert film.poster_url == "https://image.tmdb.org/t/p/w500/poster.jpg"
    assert film.release_year == 2024


async def test_enricher_leaves_film_untouched_without_match() -> None:
    client, _ = _client_returning({"results": []})
    film = _film()

    await TmdbEnricher(client).enrich(film)

    assert film.tmdb_id is None
    assert film.overview is None
