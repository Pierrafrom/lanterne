"""Two-tier geocoding for venues: address lookup, then name-search fallback.

Tier 1 (:func:`geocode_address`) resolves a known street address via the
French government's BAN/Géoplateforme geocoder — free, keyless, and precise
by construction (an address match is never ambiguous about which building it
names). Tier 2 (:func:`geocode_name`) is the fallback for a venue with no
known address at all: a plain name search against Photon (OSM-based), which
is unreliable in general (confirmed live: "UGC La Défense" resolved to the
surrounding district, not the cinema itself) — a result is only trusted
outright when OSM tags it as a cinema (``amenity=cinema``); anything else is
still returned, but flagged ``needs_review=True`` for manual confirmation via
pgweb rather than silently accepted. See
``docs/decisions/0021-venue-screen-split-and-geocoding.md`` for why these two
free services were chosen over a paid geocoding API.

:func:`geocode_venue` is the single entry point ``main.py``'s
``geocode-venues`` command uses — it runs both tiers in order and returns
whichever succeeded first.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from lanterne.core.models import GeocodeSource
from lanterne.logging_config import get_logger

logger = get_logger(__name__)

_BAN_URL = "https://data.geopf.fr/geocodage/search"
_PHOTON_URL = "https://photon.komoot.io/api/"

# OSM tagging convention for a cinema POI — the only tag combination trusted
# to auto-accept a name-search match (see this module's docstring).
_CINEMA_OSM_KEY = "amenity"
_CINEMA_OSM_VALUE = "cinema"

# ~5 req/s, respectful of both free public services' fair-use expectations.
# Applied between the two tiers within :func:`geocode_venue`, and reused by
# ``main.py``'s ``geocode-venues`` command between venues.
RATE_LIMIT_DELAY_SECONDS = 0.2

# west,south,east,north — comfortably covers the Île-de-France region this
# project's venues are all located in (confirmed live against Photon's own
# `bbox` parameter). Restricts tier 2's name search to this box; see
# :func:`geocode_name`.
_ILE_DE_FRANCE_BBOX = "1.4,48.1,3.6,49.3"


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    """One venue's resolved coordinates, and how confident the match is.

    Attributes:
        latitude: Resolved latitude.
        longitude: Resolved longitude.
        source: Which tier produced this result.
        needs_review: Whether the match should be flagged for manual
            confirmation rather than trusted outright — always False for an
            address match (tier 1, precise by construction); True for a
            name-search match (tier 2) whose OSM tag doesn't confirm it's
            actually a cinema.
        address: A formatted address derived from the match, when the
            source provided structured address components Photon
            (tier 2) does; ``None`` for a tier 1 match, since the venue's
            address was already known going in.
    """

    latitude: float
    longitude: float
    source: GeocodeSource
    needs_review: bool
    address: str | None = None


async def geocode_venue(
    client: httpx.AsyncClient, *, address: str | None, name: str
) -> GeocodeResult | None:
    """Resolve one venue's coordinates: address lookup first, name fallback.

    Args:
        client: Shared async HTTP client.
        address: The venue's known street address, or None.
        name: The venue's display name, used only when ``address`` is None
            or the address lookup found no match.

    Returns:
        The best available result, or None when neither tier found a match
        (including a tier that failed outright — see :func:`_try_tier`).
    """
    result = None
    if address is not None:
        result = await _try_tier(geocode_address, client, address)
        if result is None:
            await asyncio.sleep(RATE_LIMIT_DELAY_SECONDS)
    if result is None:
        result = await _try_tier(geocode_name, client, name)
    if result is None:
        logger.warning(
            "venue geocoding found no match on either tier",
            extra={"ctx": {"venue": name, "address": address}},
        )
    return result


async def _try_tier(
    tier: Callable[[httpx.AsyncClient, str], Awaitable[GeocodeResult | None]],
    client: httpx.AsyncClient,
    query: str,
) -> GeocodeResult | None:
    """Run one geocoding tier, treating a failed HTTP request as no match.

    Both free public services have no formal SLA — a transient timeout or
    5xx (confirmed live: the BAN API returned one 504 mid-run) must not
    abort an entire ``geocode-venues`` run over one venue, the same "one bad
    item never aborts a run" posture as
    ``io/scrapers/base.py``'s ``structure_via_llm``.

    Args:
        tier: :func:`geocode_address` or :func:`geocode_name`.
        client: Shared async HTTP client.
        query: The address or name to pass to ``tier``.

    Returns:
        ``tier``'s result, or None when it found nothing or the request
        itself failed.
    """
    try:
        return await tier(client, query)
    except httpx.HTTPError:
        logger.warning(
            "geocoding request failed",
            extra={"ctx": {"tier": tier.__name__, "query": query}},
        )
        return None


async def geocode_address(
    client: httpx.AsyncClient, address: str
) -> GeocodeResult | None:
    """Resolve a street address to coordinates via the BAN/Géoplateforme API.

    Free, keyless, official French government address geocoder — successor
    to the now-decommissioned ``api-adresse.data.gouv.fr`` (confirmed live,
    redirected/deprecated end of January 2026).

    Args:
        client: Shared async HTTP client.
        address: A street address, as stored on ``Venue.address``.

    Returns:
        The resolved coordinates (never flagged for review — an address
        match is precise by construction), or None when the API returns no
        match.
    """
    response = await client.get(_BAN_URL, params={"q": address, "limit": 1})
    response.raise_for_status()
    features = response.json().get("features")
    if not isinstance(features, list) or not features:
        return None
    coordinates = _point_coordinates(features[0])
    if coordinates is None:
        return None
    longitude, latitude = coordinates
    return GeocodeResult(
        latitude=latitude,
        longitude=longitude,
        source=GeocodeSource.ADDRESS,
        needs_review=False,
    )


async def geocode_name(client: httpx.AsyncClient, name: str) -> GeocodeResult | None:
    """Resolve a venue name to coordinates via Photon (OSM-based POI search).

    Restricted to an Île-de-France bounding box — this project covers only
    Paris/IDF cinemas, and a plain global name search is unreliable for a
    generic venue name: confirmed live, an unrestricted search for "Apollo"
    resolved to a village in Pennsylvania, "Capitole" to a square in Rome.
    The bounding box doesn't guarantee a *correct* match (a wrong IDF result
    is still possible, hence the ``needs_review`` gate below), but it
    eliminates the wildly-wrong global false positives outright.

    Args:
        client: Shared async HTTP client.
        name: The venue's display name.

    Returns:
        The best-scoring match's coordinates, flagged ``needs_review`` unless
        OSM tags it as a cinema, or None when Photon returns nothing at all.
    """
    response = await client.get(
        _PHOTON_URL,
        params={"q": name, "limit": 1, "lang": "fr", "bbox": _ILE_DE_FRANCE_BBOX},
    )
    response.raise_for_status()
    features = response.json().get("features")
    if not isinstance(features, list) or not features:
        return None
    feature = features[0]
    coordinates = _point_coordinates(feature)
    if coordinates is None:
        return None
    longitude, latitude = coordinates
    properties = feature.get("properties") if isinstance(feature, dict) else None
    properties = properties if isinstance(properties, dict) else {}
    is_cinema = (
        properties.get("osm_key") == _CINEMA_OSM_KEY
        and properties.get("osm_value") == _CINEMA_OSM_VALUE
    )
    return GeocodeResult(
        latitude=latitude,
        longitude=longitude,
        source=GeocodeSource.NAME_SEARCH,
        needs_review=not is_cinema,
        address=_format_photon_address(properties),
    )


def _point_coordinates(feature: object) -> tuple[float, float] | None:
    """Extract a GeoJSON Point feature's (longitude, latitude), or None."""
    if not isinstance(feature, dict):
        return None
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    if (
        not isinstance(coordinates, list)
        or len(coordinates) != 2
        or not all(isinstance(value, int | float) for value in coordinates)
    ):
        return None
    longitude, latitude = coordinates
    return float(longitude), float(latitude)


def _format_photon_address(properties: dict[str, Any]) -> str | None:
    """Build a human-readable address from Photon's structured properties.

    Used to backfill ``Venue.address`` for a venue that had none — a
    name-search match otherwise leaves the "every venue needs a valid
    address" goal (see
    ``docs/decisions/0021-venue-screen-split-and-geocoding.md``) unmet even
    when coordinates were found.

    Args:
        properties: The matched feature's ``properties`` object.

    Returns:
        ``"<street>, <postcode> <city>"`` (street or city alone when only one
        is known), or None when neither is present.
    """
    street = properties.get("street")
    housenumber = properties.get("housenumber")
    parts = []
    if isinstance(street, str) and street:
        parts.append(
            f"{housenumber} {street}"
            if isinstance(housenumber, str) and housenumber
            else street
        )
    postcode = properties.get("postcode")
    city = properties.get("city")
    if isinstance(city, str) and city:
        parts.append(
            f"{postcode} {city}" if isinstance(postcode, str) and postcode else city
        )
    return ", ".join(parts) if parts else None
