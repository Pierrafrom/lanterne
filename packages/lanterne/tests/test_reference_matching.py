"""Tests for the automatic-match rule in ``io/reference/matching.py``.

Every test here is pure — no database, no network — since this module is
what mechanically enforces the constraint that automatic matching is only
ever allowed when name, department, and distance all agree (see
``docs/decisions/0026-official-reference-data-layer.md``).
"""

from geoalchemy2 import WKTElement

from lanterne.core.models import Venue
from lanterne.io.reference.matching import match_establishments
from lanterne.io.reference.models import OfficialEstablishment

# Le Grand Rex's real-world coordinates, used as the "correct" anchor point
# for both the establishment and a well-geocoded venue.
_REX_LAT, _REX_LON = 48.8708, 2.3489
# A point roughly 2 km away — clearly outside the auto-match distance
# threshold, but still plausibly Paris (not used to test "far away", used
# to test "too far to trust").
_FAR_LAT, _FAR_LON = 48.89, 2.36


def _make_establishment(**overrides: object) -> OfficialEstablishment:
    defaults: dict[str, object] = {
        "cnc_id": "311",
        "name": "Grand Rex",
        "dep": "75",
        "insee_code": "75102",
        "commune": "Paris 2e Arrondissement",
        "address": None,
        "screen_count": 7,
        "seat_count": 3678,
        "art_et_essai": False,
        "art_et_essai_grade": None,
        "art_et_essai_labels": None,
        "is_multiplex": False,
        "latitude": _REX_LAT,
        "longitude": _REX_LON,
    }
    defaults.update(overrides)
    return OfficialEstablishment(**defaults)  # type: ignore[arg-type]


def _make_venue(**overrides: object) -> Venue:
    defaults: dict[str, object] = {
        "id": 1,
        "slug": "grand rex",
        "name": "Grand Rex",
        "address": "1 Boulevard Poissonnière, 75002 Paris",
        "location": WKTElement(f"POINT({_REX_LON} {_REX_LAT})", srid=4326),
    }
    defaults.update(overrides)
    return Venue(**defaults)  # type: ignore[arg-type]


def test_all_three_checks_pass_is_automatic() -> None:
    establishment = _make_establishment()
    venue = _make_venue()

    candidates = match_establishments([establishment], [venue])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.name_match
    assert candidate.department_match
    assert candidate.distance_match
    assert candidate.is_automatic


def test_name_mismatch_is_never_a_candidate() -> None:
    establishment = _make_establishment(name="Grand Rex")
    venue = _make_venue(name="Le Louxor")

    candidates = match_establishments([establishment], [venue])

    assert candidates == []


def test_department_mismatch_fails_automatic_match() -> None:
    establishment = _make_establishment(dep="93")
    venue = _make_venue()

    candidates = match_establishments([establishment], [venue])

    assert len(candidates) == 1
    assert candidates[0].name_match
    assert not candidates[0].department_match
    assert not candidates[0].is_automatic


def test_venue_with_unparseable_address_fails_department_check() -> None:
    establishment = _make_establishment()
    venue = _make_venue(address="quelque part à Paris")

    candidates = match_establishments([establishment], [venue])

    assert not candidates[0].department_match
    assert not candidates[0].is_automatic


def test_venue_with_no_address_fails_department_check() -> None:
    establishment = _make_establishment()
    venue = _make_venue(address=None)

    candidates = match_establishments([establishment], [venue])

    assert not candidates[0].department_match
    assert not candidates[0].is_automatic


def test_distance_too_far_fails_automatic_match() -> None:
    establishment = _make_establishment(latitude=_FAR_LAT, longitude=_FAR_LON)
    venue = _make_venue()

    candidates = match_establishments([establishment], [venue])

    assert len(candidates) == 1
    assert candidates[0].name_match
    assert candidates[0].department_match
    assert not candidates[0].distance_match
    assert not candidates[0].is_automatic
    assert candidates[0].distance_km is not None
    assert candidates[0].distance_km > 0.3


def test_establishment_with_no_coordinates_never_auto_matches() -> None:
    establishment = _make_establishment(latitude=None, longitude=None)
    venue = _make_venue()

    candidates = match_establishments([establishment], [venue])

    assert not candidates[0].distance_match
    assert candidates[0].distance_km is None
    assert not candidates[0].is_automatic


def test_venue_with_no_location_never_auto_matches() -> None:
    establishment = _make_establishment()
    venue = _make_venue(location=None)

    candidates = match_establishments([establishment], [venue])

    assert not candidates[0].distance_match
    assert not candidates[0].is_automatic


def test_a_venue_already_linked_to_a_different_cnc_id_is_skipped() -> None:
    establishment = _make_establishment(cnc_id="311")
    venue = _make_venue(cnc_id="999")

    candidates = match_establishments([establishment], [venue])

    assert candidates == []


def test_an_establishment_already_matched_by_another_venue_is_skipped() -> None:
    establishment = _make_establishment(cnc_id="311")
    already_matched_venue = _make_venue(id=2, cnc_id="311")
    new_venue = _make_venue(id=3, cnc_id=None)

    candidates = match_establishments(
        [establishment], [already_matched_venue, new_venue]
    )

    assert candidates == []


def test_leading_article_is_stripped_before_comparing_names() -> None:
    establishment = _make_establishment(name="Grand Rex")
    venue = _make_venue(name="Le Grand Rex")

    candidates = match_establishments([establishment], [venue])

    assert len(candidates) == 1
    assert candidates[0].name_match
    assert candidates[0].is_automatic


def test_two_venues_matching_one_establishment_only_lower_id_is_automatic() -> None:
    establishment = _make_establishment()
    first_venue = _make_venue(id=10)
    second_venue = _make_venue(id=5)

    candidates = match_establishments([establishment], [first_venue, second_venue])

    by_venue_id = {candidate.venue.id: candidate for candidate in candidates}
    assert by_venue_id[5].is_automatic
    assert not by_venue_id[5].is_duplicate
    assert not by_venue_id[10].is_automatic
    assert by_venue_id[10].is_duplicate
