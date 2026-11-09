"""Great-circle distance between two points, in kilometres.

Used by ``io/reference/matching.py`` to compare a scraped :class:`Venue`'s
geocoded ``location`` (see ``docs/decisions/0021-venue-screen-split-and-geocoding.md``)
against an official establishment's coordinates, as one leg of the
automatic-match rule (see ``docs/decisions/0026-official-reference-data-layer.md``).
"""

import math

_EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance between two coordinates, in km.

    Args:
        lat1: First point's latitude, in decimal degrees.
        lon1: First point's longitude, in decimal degrees.
        lat2: Second point's latitude, in decimal degrees.
        lon2: Second point's longitude, in decimal degrees.

    Returns:
        The distance between the two points along the Earth's surface, in
        kilometres.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))
