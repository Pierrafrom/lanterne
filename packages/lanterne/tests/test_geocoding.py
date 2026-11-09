"""Tests for the two-tier venue geocoding (httpx mocked, no real network)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx

from lanterne.core.models import GeocodeSource
from lanterne.io.geocoding import geocode_address, geocode_name, geocode_venue


def _feature(coordinates: list[float], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coordinates},
        "properties": properties,
    }


def _client(*payloads: dict[str, Any]) -> MagicMock:
    responses = []
    for payload in payloads:
        response = MagicMock()
        response.json = MagicMock(return_value=payload)
        response.raise_for_status = MagicMock()
        responses.append(response)
    http = MagicMock()
    http.get = AsyncMock(side_effect=responses)
    return http


async def test_geocode_address_maps_the_first_feature_to_a_precise_result() -> None:
    payload = {
        "features": [
            _feature([2.3522, 48.8566], {"label": "1 Rue de Rivoli 75001 Paris"})
        ]
    }
    client = _client(payload)

    result = await geocode_address(client, "1 Rue de Rivoli 75001 Paris")

    assert result is not None
    assert result.latitude == 48.8566
    assert result.longitude == 2.3522
    assert result.source is GeocodeSource.ADDRESS
    assert result.needs_review is False
    assert result.address is None


async def test_geocode_address_returns_none_when_no_feature_matches() -> None:
    client = _client({"features": []})

    result = await geocode_address(client, "somewhere unresolvable")

    assert result is None


async def test_geocode_name_trusts_a_result_osm_tags_as_a_cinema() -> None:
    payload = {
        "features": [
            _feature(
                [2.3522, 48.8566],
                {
                    "osm_key": "amenity",
                    "osm_value": "cinema",
                    "housenumber": "12",
                    "street": "Rue du Cinéma",
                    "postcode": "75001",
                    "city": "Paris",
                },
            )
        ]
    }
    client = _client(payload)

    result = await geocode_name(client, "Le Grand Rex")

    assert result is not None
    assert result.source is GeocodeSource.NAME_SEARCH
    assert result.needs_review is False
    assert result.address == "12 Rue du Cinéma, 75001 Paris"


async def test_geocode_name_flags_a_non_cinema_match_for_review() -> None:
    # Confirmed live: "UGC La Défense" resolves to the surrounding district
    # (osm_key="place"), not the cinema itself — must not be auto-trusted.
    payload = {
        "features": [
            _feature([2.2367, 48.8917], {"osm_key": "place", "osm_value": "suburb"})
        ]
    }
    client = _client(payload)

    result = await geocode_name(client, "UGC La Défense")

    assert result is not None
    assert result.needs_review is True
    assert result.address is None


async def test_geocode_name_returns_none_when_photon_finds_nothing() -> None:
    client = _client({"features": []})

    result = await geocode_name(client, "Cinéma Introuvable")

    assert result is None


async def test_geocode_name_restricts_the_search_to_ile_de_france() -> None:
    # Confirmed live: an unrestricted search for "Apollo" resolves to a
    # village in Pennsylvania — the bbox param must always be sent.
    client = _client({"features": []})

    await geocode_name(client, "Apollo")

    params = client.get.call_args.kwargs["params"]
    assert params["bbox"] == "1.4,48.1,3.6,49.3"


async def test_geocode_venue_prefers_the_address_tier_when_it_succeeds() -> None:
    payload = {"features": [_feature([2.35, 48.85], {})]}
    client = _client(payload)

    result = await geocode_venue(client, address="1 Rue de Rivoli", name="Le Rex")

    assert result is not None
    assert result.source is GeocodeSource.ADDRESS
    client.get.assert_awaited_once()


async def test_geocode_venue_falls_back_to_name_search_when_address_fails() -> None:
    cinema_tags = {"osm_key": "amenity", "osm_value": "cinema"}
    client = _client(
        {"features": []},
        {"features": [_feature([2.35, 48.85], cinema_tags)]},
    )

    result = await geocode_venue(client, address="Unresolvable Address", name="Le Rex")

    assert result is not None
    assert result.source is GeocodeSource.NAME_SEARCH
    assert client.get.await_count == 2


async def test_geocode_venue_skips_the_address_tier_when_none_is_known() -> None:
    payload = {"features": [_feature([2.35, 48.85], {})]}
    client = _client(payload)

    result = await geocode_venue(client, address=None, name="Le Rex")

    assert result is not None
    assert result.source is GeocodeSource.NAME_SEARCH
    client.get.assert_awaited_once()


async def test_geocode_venue_returns_none_when_neither_tier_matches() -> None:
    client = _client({"features": []}, {"features": []})

    result = await geocode_venue(client, address="Nowhere", name="Ghost Cinema")

    assert result is None


async def test_geocode_venue_falls_back_when_the_address_tier_fails_outright() -> None:
    # Confirmed live: the BAN API returned a transient 504 mid-run — a
    # single venue's HTTP failure must not propagate and abort a whole
    # geocode-venues run.
    error_response = MagicMock()
    error_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "Gateway Timeout", request=MagicMock(), response=MagicMock()
        )
    )
    cinema_tags = {"osm_key": "amenity", "osm_value": "cinema"}
    ok_response = MagicMock()
    ok_response.json = MagicMock(
        return_value={"features": [_feature([2.35, 48.85], cinema_tags)]}
    )
    ok_response.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(side_effect=[error_response, ok_response])

    result = await geocode_venue(client, address="Unresolvable Address", name="Le Rex")

    assert result is not None
    assert result.source is GeocodeSource.NAME_SEARCH
    assert client.get.await_count == 2


async def test_geocode_venue_returns_none_when_every_tier_fails_outright() -> None:
    error_response = MagicMock()
    error_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "Gateway Timeout", request=MagicMock(), response=MagicMock()
        )
    )
    client = MagicMock()
    client.get = AsyncMock(return_value=error_response)

    result = await geocode_venue(client, address="Nowhere", name="Ghost Cinema")

    assert result is None
