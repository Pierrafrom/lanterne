"""Tests for parsing the CNC's active-establishments XLSX workbook."""

import io
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from openpyxl import Workbook

from lanterne.io.reference.cnc_venues import (
    fetch_cnc_active_establishments,
    parse_cnc_active_establishments,
)

_HEADER = [
    "NAutoC",
    "NomEtab",
    "Ecrans",
    "fauteuils",
    "DEPCOM",
    "COMMUNE",
    "genre",
    "AE",
    "3D",
]


def _build_workbook(*, sheet_names: list[str], rows: list[list[object]]) -> Workbook:
    """Build a workbook whose last sheet name holds a realistic CNC layout.

    Mirrors the real file's shape: a few blank/title rows before the header,
    confirmed live 2026-08-03 (header on row 5 of the ``2025`` sheet).
    """
    workbook = Workbook()
    default_sheet = workbook.active
    assert default_sheet is not None
    workbook.remove(default_sheet)
    for name in sheet_names:
        sheet = workbook.create_sheet(name)
        if name.isdigit():
            sheet.append([])
            sheet.append(["LISTE DES ETABLISSEMENTS ACTIFS"])
            sheet.append([])
            sheet.append([])
            sheet.append(_HEADER)
            for row in rows:
                sheet.append(row)
    return workbook


def test_parses_a_data_row_into_an_establishment() -> None:
    workbook = _build_workbook(
        sheet_names=["Sommaire", "2025"],
        rows=[
            [
                "55",
                "ELYSEES LINCOLN",
                3,
                448,
                "75108",
                "Paris 8e Arrondissement",
                "FIXE",
                "OUI",
                "NON",
            ]
        ],
    )

    establishments = parse_cnc_active_establishments(workbook)

    assert len(establishments) == 1
    establishment = establishments[0]
    assert establishment.cnc_id == "55"
    assert establishment.name == "ELYSEES LINCOLN"
    assert establishment.dep == "75"
    assert establishment.insee_code == "75108"
    assert establishment.commune == "Paris 8e Arrondissement"
    assert establishment.screen_count == 3
    assert establishment.seat_count == 448
    assert establishment.art_et_essai is True


def test_picks_the_highest_numbered_year_sheet() -> None:
    workbook = _build_workbook(
        sheet_names=["Sommaire", "2024", "2025", "ESRI_MAPINFO_SHEET"],
        rows=[
            ["1", "ONLY IN LATEST", 1, 1, "75001", "Paris 1er", "FIXE", "NON", "NON"]
        ],
    )
    # The 2024 sheet only gets a header, no data row, so picking the wrong
    # sheet would yield zero establishments instead of one.
    workbook["2024"].append(_HEADER)

    establishments = parse_cnc_active_establishments(workbook)

    assert [establishment.name for establishment in establishments] == [
        "ONLY IN LATEST"
    ]


def test_strips_whitespace_from_name_and_id() -> None:
    workbook = _build_workbook(
        sheet_names=["2025"],
        rows=[
            [" 204", " UGC OPERA", 4, 722, "75109", "Paris 9e", "FIXE", "NON", "NON"]
        ],
    )

    establishments = parse_cnc_active_establishments(workbook)

    assert establishments[0].cnc_id == "204"
    assert establishments[0].name == "UGC OPERA"


def test_ae_non_maps_to_false() -> None:
    workbook = _build_workbook(
        sheet_names=["2025"],
        rows=[
            ["1", "NOT ART ET ESSAI", 1, 1, "75001", "Paris 1er", "FIXE", "NON", "NON"]
        ],
    )

    establishments = parse_cnc_active_establishments(workbook)

    assert establishments[0].art_et_essai is False


async def test_fetch_resolves_the_current_xlsx_resource_and_parses_it() -> None:
    workbook = _build_workbook(
        sheet_names=["2025"],
        rows=[
            ["55", "ELYSEES LINCOLN", 3, 448, "75108", "Paris 8e", "FIXE", "OUI", "NON"]
        ],
    )
    buffer = io.BytesIO()
    workbook.save(buffer)
    xlsx_bytes = buffer.getvalue()

    def _response(
        *, json_data: dict[str, Any] | None = None, content: bytes = b""
    ) -> MagicMock:
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value=json_data)
        response.content = content
        return response

    async def fake_get(url: str, **_: Any) -> MagicMock:
        if url.endswith("/datasets/5437eb4e88ee387cb58f5e86/"):
            return _response(
                json_data={
                    "resources": [{"format": "xlsx", "url": "https://fake/cnc.xlsx"}]
                }
            )
        assert url == "https://fake/cnc.xlsx"
        return _response(content=xlsx_bytes)

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)

    establishments = await fetch_cnc_active_establishments(client)

    assert len(establishments) == 1
    assert establishments[0].cnc_id == "55"
