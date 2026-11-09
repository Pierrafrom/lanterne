"""The CNC's official "active establishments" census.

Published by the CNC on data.gouv.fr (dataset id ``5437eb4e88ee387cb58f5e86``,
"Liste des établissements cinématographiques actifs"), one sheet per year in
a single XLSX workbook. Confirmed live 2026-08-03: sheet ``2025`` holds 2065
rows, header on row 5 (``NAutoC, NomEtab, Ecrans, fauteuils, DEPCOM, COMMUNE,
genre, AE, 3D``). See ``docs/decisions/0026-official-reference-data-layer.md``.
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from openpyxl import load_workbook
from openpyxl.workbook import Workbook

_DATASET_API_URL = "https://www.data.gouv.fr/api/1/datasets/5437eb4e88ee387cb58f5e86/"
_NON_YEAR_SHEETS = {"Sommaire", "ESRI_MAPINFO_SHEET"}
_HEADER_MARKER_COLUMN = "NAutoC"


@dataclass(frozen=True, slots=True)
class CncEstablishment:
    """One establishment, as the CNC master list reports it.

    Attributes:
        cnc_id: The CNC's own establishment authorization number
            (``NAutoC``).
        name: Establishment name (``NomEtab``).
        dep: Département code, the first two characters of ``insee_code``.
        insee_code: INSEE commune code (``DEPCOM``).
        commune: Commune name (``COMMUNE``).
        screen_count: Total screens (``Ecrans``), when known.
        seat_count: Total seats (``fauteuils``), when known.
        art_et_essai: Whether the CNC classifies this establishment as
            Art et Essai (``AE == "OUI"``).
    """

    cnc_id: str
    name: str
    dep: str
    insee_code: str
    commune: str
    screen_count: int | None
    seat_count: int | None
    art_et_essai: bool


def parse_cnc_active_establishments(workbook: Workbook) -> list[CncEstablishment]:
    """Parse the CNC master list's most recent year sheet into establishments.

    Pure — no I/O. Locates the header row rather than assuming a fixed
    offset, since the CNC's own workbook has several blank/title rows above
    it that could shift between releases.

    Args:
        workbook: The loaded XLSX workbook (see
            :func:`fetch_cnc_active_establishments`).

    Returns:
        One :class:`CncEstablishment` per data row of the latest year sheet.
    """
    sheet = workbook[_latest_year_sheet(workbook.sheetnames)]
    rows = list(sheet.iter_rows(values_only=True))
    header_index = next(
        index
        for index, row in enumerate(rows)
        if row and row[0] == _HEADER_MARKER_COLUMN
    )
    columns = {str(name): index for index, name in enumerate(rows[header_index])}
    return [
        _row_to_establishment(row, columns)
        for row in rows[header_index + 1 :]
        if row and row[columns[_HEADER_MARKER_COLUMN]] is not None
    ]


def _latest_year_sheet(sheet_names: Sequence[str]) -> str:
    """Return the highest-numbered year sheet, skipping summary/GIS sheets."""
    year_sheets = [name for name in sheet_names if name not in _NON_YEAR_SHEETS]
    return max((name for name in year_sheets if name.isdigit()), key=int)


def _row_to_establishment(
    row: tuple[object, ...], columns: dict[str, int]
) -> CncEstablishment:
    """Map one data row to a :class:`CncEstablishment`, by column name."""
    insee_code = str(row[columns["DEPCOM"]]).strip()
    return CncEstablishment(
        cnc_id=str(row[columns["NAutoC"]]).strip(),
        name=str(row[columns["NomEtab"]]).strip(),
        dep=insee_code[:2],
        insee_code=insee_code,
        commune=str(row[columns["COMMUNE"]]).strip(),
        screen_count=_to_int(row[columns["Ecrans"]]),
        seat_count=_to_int(row[columns["fauteuils"]]),
        art_et_essai=row[columns["AE"]] == "OUI",
    )


def _to_int(value: object) -> int | None:
    """Cast an XLSX numeric cell (often a float) to ``int``, or ``None``."""
    return int(value) if isinstance(value, int | float) else None


async def fetch_cnc_active_establishments(
    client: httpx.AsyncClient,
) -> list[CncEstablishment]:
    """Fetch and parse the CNC's current active-establishments list.

    Resolves the dataset's current XLSX resource via the data.gouv.fr
    dataset API rather than a hardcoded resource URL — the dataset id is
    stable, but the CNC republishes the file in place and a hardcoded
    resource id/URL could rotate out from under a fixed link.

    Args:
        client: Shared async HTTP client.

    Returns:
        Every establishment on the latest year sheet.
    """
    dataset_response = await client.get(_DATASET_API_URL)
    dataset_response.raise_for_status()
    resources = dataset_response.json()["resources"]
    xlsx_url = next(
        resource["url"] for resource in resources if resource["format"] == "xlsx"
    )
    xlsx_response = await client.get(xlsx_url)
    xlsx_response.raise_for_status()
    workbook = load_workbook(
        io.BytesIO(xlsx_response.content), read_only=True, data_only=True
    )
    return parse_cnc_active_establishments(workbook)
