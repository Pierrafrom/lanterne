"""Tests for the TMDB client and the film enricher (httpx mocked)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cine_event_bot.core.models import Film
from cine_event_bot.io.tmdb import TmdbClient, TmdbEnricher


def _search_payload(**overrides: Any) -> dict[str, Any]:
    result = {"id": 693134, "title": "Dune: Part Two"}
    result.update(overrides)
    return {"results": [result]}


def _details_payload(**overrides: Any) -> dict[str, Any]:
    details = {
        "id": 693134,
        "title": "Dune, deuxième partie",
        "original_title": "Dune: Part Two",
        "overview": "Paul Atreides unites with the Fremen.",
        "poster_path": "/poster.jpg",
        "backdrop_path": "/backdrop.jpg",
        "imdb_id": "tt15239678",
        "release_date": "2024-02-27",
        "runtime": 167,
        "vote_average": 8.2,
        "genres": [
            {"id": 878, "name": "Science-Fiction"},
            {"id": 12, "name": "Aventure"},
        ],
        "credits": {
            "crew": [
                {"name": "Denis Villeneuve", "job": "Director"},
                {"name": "Greig Fraser", "job": "Director of Photography"},
            ]
        },
    }
    details.update(overrides)
    return details


def _client(
    search: dict[str, Any] | None = None, details: dict[str, Any] | None = None
) -> tuple[TmdbClient, MagicMock]:
    responses = []
    for payload in (search or _search_payload(), details or _details_payload()):
        response = MagicMock()
        response.json = MagicMock(return_value=payload)
        response.raise_for_status = MagicMock()
        responses.append(response)
    http = MagicMock()
    http.get = AsyncMock(side_effect=responses)
    return TmdbClient(http, api_key="key"), http


def _details_only_client(
    details: dict[str, Any] | None = None, *, status_error: int | None = None
) -> tuple[TmdbClient, MagicMock]:
    response = MagicMock()
    if status_error is not None:
        error = httpx.HTTPStatusError(
            "not found",
            request=MagicMock(),
            response=MagicMock(status_code=status_error),
        )
        response.raise_for_status = MagicMock(side_effect=error)
    else:
        response.json = MagicMock(return_value=details or _details_payload())
        response.raise_for_status = MagicMock()
    http = MagicMock()
    http.get = AsyncMock(return_value=response)
    return TmdbClient(http, api_key="key"), http


async def test_find_maps_search_and_details() -> None:
    client, _ = _client()

    match = await client.find("Dune: Part Two")

    assert match is not None
    assert match.tmdb_id == 693134
    assert match.original_title == "Dune: Part Two"
    assert match.director == "Denis Villeneuve"
    assert match.release_year == 2024
    assert match.runtime_minutes == 167
    assert match.genres == ("Science-Fiction", "Aventure")
    assert match.overview == "Paul Atreides unites with the Fremen."
    assert match.poster_url == "https://image.tmdb.org/t/p/w500/poster.jpg"
    assert match.backdrop_url == "https://image.tmdb.org/t/p/w1280/backdrop.jpg"
    assert match.imdb_id == "tt15239678"
    assert match.vote_average == 8.2


async def test_find_queries_search_then_details_with_credits() -> None:
    client, http = _client()

    await client.find("Mulholland Drive")

    search_call, details_call = http.get.await_args_list
    assert search_call.kwargs["params"]["query"] == "Mulholland Drive"
    assert search_call.kwargs["params"]["api_key"] == "key"
    assert "/movie/693134" in details_call.args[0]
    assert details_call.kwargs["params"]["append_to_response"] == "credits"


async def test_find_returns_none_when_no_results() -> None:
    client, http = _client(search={"results": []})

    assert await client.find("Unknown film") is None
    http.get.assert_awaited_once()  # no details call without a match


async def test_get_by_id_fetches_details_directly_with_no_search_call() -> None:
    client, http = _details_only_client()

    match = await client.get_by_id(693134)

    assert match is not None
    assert match.tmdb_id == 693134
    assert match.imdb_id == "tt15239678"
    assert match.backdrop_url == "https://image.tmdb.org/t/p/w1280/backdrop.jpg"
    http.get.assert_awaited_once()
    assert "/movie/693134" in http.get.await_args.args[0]


async def test_get_by_id_returns_none_on_a_404() -> None:
    client, _ = _details_only_client(status_error=404)

    assert await client.get_by_id(999999) is None


async def test_get_by_id_reraises_a_non_404_error() -> None:
    client, _ = _details_only_client(status_error=500)

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_by_id(693134)


async def test_find_handles_missing_optional_fields() -> None:
    client, _ = _client(
        details=_details_payload(
            poster_path=None,
            backdrop_path=None,
            imdb_id=None,
            release_date="",
            runtime=None,
            vote_average=None,
            genres=[],
            credits={"crew": []},
            original_title=None,
            overview="",
        )
    )

    match = await client.find("Dune")

    assert match is not None
    assert match.poster_url is None
    assert match.backdrop_url is None
    assert match.imdb_id is None
    assert match.release_year is None
    assert match.runtime_minutes is None
    assert match.vote_average is None
    assert match.genres is None
    assert match.director is None
    assert match.original_title is None
    assert match.overview is None


async def test_find_joins_multiple_directors() -> None:
    client, _ = _client(
        details=_details_payload(
            credits={
                "crew": [
                    {"name": "Lana Wachowski", "job": "Director"},
                    {"name": "Lilly Wachowski", "job": "Director"},
                ]
            }
        )
    )

    match = await client.find("Matrix")

    assert match is not None
    assert match.director == "Lana Wachowski, Lilly Wachowski"


async def test_enricher_fills_every_film_field() -> None:
    client, _ = _client()
    film = Film(title_key="dune: part two", title="Dune: Part Two")

    await TmdbEnricher(client).enrich(film)

    assert film.tmdb_id == 693134
    assert film.original_title == "Dune: Part Two"
    assert film.director == "Denis Villeneuve"
    assert film.release_year == 2024
    assert film.runtime_minutes == 167
    assert film.genres == ["Science-Fiction", "Aventure"]
    assert film.overview == "Paul Atreides unites with the Fremen."
    assert film.poster_url == "https://image.tmdb.org/t/p/w500/poster.jpg"
    assert film.backdrop_url == "https://image.tmdb.org/t/p/w1280/backdrop.jpg"
    assert film.imdb_id == "tt15239678"
    assert film.vote_average == 8.2


async def test_enricher_leaves_film_untouched_without_match() -> None:
    client, _ = _client(search={"results": []})
    film = Film(title_key="dune", title="Dune")

    await TmdbEnricher(client).enrich(film)

    assert film.tmdb_id is None
    assert film.overview is None
    assert film.director is None
