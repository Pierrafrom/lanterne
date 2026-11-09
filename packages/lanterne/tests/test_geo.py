"""Tests for the pure haversine distance helper."""

from lanterne.core.geo import haversine_km

# Notre-Dame de Paris and Sacré-Cœur, a well-known ~4.5 km straight-line
# distance (confirmed via multiple online distance calculators).
_NOTRE_DAME = (48.852968, 2.349902)
_SACRE_COEUR = (48.886705, 2.343104)


def test_haversine_km_returns_zero_for_identical_points() -> None:
    assert haversine_km(48.8566, 2.3522, 48.8566, 2.3522) == 0.0


def test_haversine_km_matches_known_paris_landmark_distance() -> None:
    distance = haversine_km(*_NOTRE_DAME, *_SACRE_COEUR)

    assert 3.6 < distance < 3.9


def test_haversine_km_is_symmetric() -> None:
    forward = haversine_km(*_NOTRE_DAME, *_SACRE_COEUR)
    backward = haversine_km(*_SACRE_COEUR, *_NOTRE_DAME)

    assert forward == backward
