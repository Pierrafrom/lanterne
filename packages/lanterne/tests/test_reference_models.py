"""Tests for merging the CNC master list with the Île-de-France complement."""

from lanterne.io.reference.cnc_venues import CncEstablishment
from lanterne.io.reference.idf_venues import IdfEstablishment
from lanterne.io.reference.models import merge_establishments


def _cnc(**overrides: object) -> CncEstablishment:
    defaults: dict[str, object] = {
        "cnc_id": "311",
        "name": "GRAND REX",
        "dep": "75",
        "insee_code": "75102",
        "commune": "Paris 2e Arrondissement",
        "screen_count": 7,
        "seat_count": 3678,
        "art_et_essai": False,
    }
    defaults.update(overrides)
    return CncEstablishment(**defaults)  # type: ignore[arg-type]


def _idf(**overrides: object) -> IdfEstablishment:
    defaults: dict[str, object] = {
        "cnc_id": "311",
        "name": "GRAND REX",
        "dep": "75",
        "insee_code": "75102",
        "commune": "Paris 2e Arrondissement",
        "address": "1 Boulevard Poissonnière",
        "screen_count": 7,
        "seat_count": 3678,
        "art_et_essai": False,
        "art_et_essai_grade": None,
        "art_et_essai_labels": None,
        "is_multiplex": True,
        "latitude": 48.8708,
        "longitude": 2.3489,
    }
    defaults.update(overrides)
    return IdfEstablishment(**defaults)  # type: ignore[arg-type]


def test_merges_by_cnc_id_enriching_from_the_idf_file() -> None:
    merged = merge_establishments([_cnc()], [_idf()])

    assert len(merged) == 1
    establishment = merged[0]
    assert establishment.cnc_id == "311"
    assert establishment.address == "1 Boulevard Poissonnière"
    assert establishment.latitude == 48.8708
    assert establishment.is_multiplex is True
    assert establishment.is_active is True


def test_cnc_only_establishment_has_no_coordinates() -> None:
    merged = merge_establishments([_cnc()], [])

    assert merged[0].address is None
    assert merged[0].latitude is None
    assert merged[0].longitude is None


def test_idf_only_establishment_absent_from_cnc_master_is_dropped() -> None:
    merged = merge_establishments([], [_idf(cnc_id="999")])

    assert merged == []


def test_establishment_outside_ile_de_france_is_dropped() -> None:
    merged = merge_establishments([_cnc(dep="69", insee_code="69001")], [])

    assert merged == []


def test_cnc_screen_count_wins_when_both_datasets_have_it() -> None:
    merged = merge_establishments([_cnc(screen_count=8)], [_idf(screen_count=7)])

    assert merged[0].screen_count == 8
