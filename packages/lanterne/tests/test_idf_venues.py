"""Tests for parsing the Île-de-France establishment JSON complement."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from lanterne.io.reference.idf_venues import (
    fetch_idf_establishments,
    parse_idf_establishments,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "idf_venues_sample.json"


def _load_payload() -> list[dict[str, object]]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_parses_every_record_in_the_fixture() -> None:
    establishments = parse_idf_establishments(_load_payload())

    assert [establishment.cnc_id for establishment in establishments] == [
        "55",
        "311",
        "481",
        "9008",
    ]


def test_float_casts_coordinates() -> None:
    establishments = parse_idf_establishments(_load_payload())
    lincoln = establishments[0]

    assert lincoln.latitude == 48.870512
    assert lincoln.longitude == 2.303157


def test_missing_coordinates_are_none_not_a_crash() -> None:
    establishments = parse_idf_establishments(_load_payload())
    parnasse = establishments[2]

    assert parnasse.latitude is None
    assert parnasse.longitude is None


def test_maps_multiplexe_and_ae_to_bool() -> None:
    establishments = parse_idf_establishments(_load_payload())
    lincoln, rex, _parnasse, _jdp = establishments

    assert lincoln.art_et_essai is True
    assert lincoln.is_multiplex is False
    assert rex.art_et_essai is False
    assert rex.is_multiplex is True


def test_wraps_label_art_et_essai_into_a_list() -> None:
    establishments = parse_idf_establishments(_load_payload())

    assert establishments[0].art_et_essai_labels == ["RD"]
    assert establishments[0].art_et_essai_grade == "A"


def test_absent_label_and_grade_are_none() -> None:
    establishments = parse_idf_establishments(_load_payload())

    assert establishments[2].art_et_essai_labels is None
    assert establishments[2].art_et_essai_grade is None


def test_non_grade_maps_to_none_not_a_literal_non_value() -> None:
    # Confirmed live (2026-08-03): a non-Art-et-Essai establishment carries
    # the literal string "NON" in categorie_art_et_essai rather than a blank
    # field — Venue.art_et_essai already carries this yes/no signal, so
    # "NON" is not a real grade and must not be stored as one (it isn't a
    # valid ArtEtEssaiGrade member and would fail the DB's enum-backed
    # column otherwise).
    establishments = parse_idf_establishments(_load_payload())
    rex = establishments[1]

    assert rex.art_et_essai_grade is None


def test_grade_e_is_accepted() -> None:
    establishments = parse_idf_establishments(_load_payload())

    assert establishments[3].art_et_essai_grade == "E"


def test_concatenated_label_code_is_decomposed() -> None:
    # Confirmed live (2026-08-03): label_art_et_essai concatenates codes
    # with no separator (e.g. "RDJP" == RD + JP).
    establishments = parse_idf_establishments(_load_payload())

    assert establishments[3].art_et_essai_labels == ["RD", "JP"]


def test_empty_address_string_becomes_none() -> None:
    establishments = parse_idf_establishments(_load_payload())

    assert establishments[2].address is None


async def test_fetch_resolves_the_current_json_resource_and_parses_it() -> None:
    payload = _load_payload()

    def _response(*, json_data: object = None) -> MagicMock:
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value=json_data)
        return response

    async def fake_get(url: str, **_: Any) -> MagicMock:
        if url.endswith("/datasets/59591ec2a3a7291dd09c8166/"):
            return _response(
                json_data={
                    "resources": [{"format": "json", "url": "https://fake/idf.json"}]
                }
            )
        assert url == "https://fake/idf.json"
        return _response(json_data=payload)

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)

    establishments = await fetch_idf_establishments(client)

    assert len(establishments) == 4
