"""The Région Île-de-France's cinema-establishment complement.

Published by Région Île-de-France on data.gouv.fr (dataset id
``59591ec2a3a7291dd09c8166``, "Établissements cinématographiques"), as a
JSON resource. Confirmed live 2026-08-03: 311 records, sharing the CNC
master list's own establishment id (``ndeg_auto`` == ``NAutoC``) — e.g. id
``"55"`` is "ELYSEES LINCOLN" in both files. This file supplies address,
coordinates, and Art et Essai grade/labels the CNC master list lacks; its
content vintage is roughly 2022, older than the CNC master list. See
``docs/decisions/0026-official-reference-data-layer.md``.
"""

from dataclasses import dataclass
from typing import Any

import httpx

_DATASET_API_URL = "https://www.data.gouv.fr/api/1/datasets/59591ec2a3a7291dd09c8166/"

# Confirmed live against the full dataset (2026-08-03): categorie_art_et_essai
# ranges A-E; a non-Art-et-Essai establishment carries the literal string
# "NON" here rather than leaving the field blank — anything outside this set
# is treated as "no grade known" (Venue.art_et_essai already carries the
# yes/no signal separately, so "NON" needs no grade of its own).
_VALID_GRADES = frozenset({"A", "B", "C", "D", "E"})

# label_art_et_essai concatenates these three atomic codes with no
# separator (confirmed live: real values include "RDJP", "JPPR", "RDPR",
# "RDJPPR" alongside the single codes) — containment-checking each code in
# a fixed order correctly decomposes every observed combination.
_ART_ET_ESSAI_LABEL_CODES = ("RD", "JP", "PR")


@dataclass(frozen=True, slots=True)
class IdfEstablishment:
    """One establishment, as the Île-de-France file reports it.

    Attributes:
        cnc_id: The CNC's own establishment authorization number
            (``ndeg_auto`` — the same id space as ``CncEstablishment.cnc_id``).
        name: Establishment name (``nom``).
        dep: Département code (``dep``).
        insee_code: INSEE commune code (``code_insee``).
        commune: Commune name (``commune``).
        address: Street address (``adresse``), when non-empty.
        screen_count: Total screens (``ecrans``), when known.
        seat_count: Total seats (``fauteuils``), when known.
        art_et_essai: Whether classified Art et Essai (``ae == "OUI"``).
        art_et_essai_grade: The CNC's Art et Essai grade
            (``categorie_art_et_essai``: A through E), when known — see
            :class:`~lanterne.core.models.ArtEtEssaiGrade`.
        art_et_essai_labels: Finer-grained Art et Essai labels, decomposed
            from ``label_art_et_essai``'s concatenated code (e.g.
            ``"RDJP"`` becomes ``["RD", "JP"]``) — confirmed live to
            combine up to all three of ``RD``/``JP``/``PR`` with no
            separator.
        is_multiplex: Whether classified a multiplex (``multiplexe == "OUI"``).
        latitude: Decimal-degrees latitude, when known.
        longitude: Decimal-degrees longitude, when known.
    """

    cnc_id: str
    name: str
    dep: str
    insee_code: str
    commune: str
    address: str | None
    screen_count: int | None
    seat_count: int | None
    art_et_essai: bool
    art_et_essai_grade: str | None
    art_et_essai_labels: list[str] | None
    is_multiplex: bool
    latitude: float | None
    longitude: float | None


def parse_idf_establishments(payload: list[dict[str, Any]]) -> list[IdfEstablishment]:
    """Parse the Île-de-France JSON payload into establishments.

    Pure — no I/O.

    Args:
        payload: The decoded JSON array (see :func:`fetch_idf_establishments`).

    Returns:
        One :class:`IdfEstablishment` per record.
    """
    return [_record_to_establishment(record) for record in payload]


def _record_to_establishment(record: dict[str, Any]) -> IdfEstablishment:
    """Map one JSON record to an :class:`IdfEstablishment`."""
    address = record.get("adresse") or None
    grade = record.get("categorie_art_et_essai")
    return IdfEstablishment(
        cnc_id=str(record["ndeg_auto"]).strip(),
        name=str(record["nom"]).strip(),
        dep=str(record["dep"]),
        insee_code=str(record["code_insee"]),
        commune=str(record["commune"]),
        address=address,
        screen_count=_to_int(record.get("ecrans")),
        seat_count=_to_int(record.get("fauteuils")),
        art_et_essai=record.get("ae") == "OUI",
        art_et_essai_grade=grade if grade in _VALID_GRADES else None,
        art_et_essai_labels=_split_labels(record.get("label_art_et_essai")),
        is_multiplex=record.get("multiplexe") == "OUI",
        latitude=_to_float(record.get("latitude")),
        longitude=_to_float(record.get("longitude")),
    )


def _split_labels(raw_label: object) -> list[str] | None:
    """Decompose a concatenated Art et Essai label code into its parts."""
    if not isinstance(raw_label, str) or not raw_label:
        return None
    labels = [code for code in _ART_ET_ESSAI_LABEL_CODES if code in raw_label]
    return labels or None


def _to_int(value: object) -> int | None:
    """Cast a numeric field (often a JSON float) to ``int``, or ``None``."""
    return int(value) if isinstance(value, int | float) else None


def _to_float(value: object) -> float | None:
    """Cast a coordinate field (a string in this payload) to ``float``."""
    return (
        float(value) if isinstance(value, str | int | float) and value != "" else None
    )


async def fetch_idf_establishments(client: httpx.AsyncClient) -> list[IdfEstablishment]:
    """Fetch and parse the Île-de-France establishment complement.

    Resolves the dataset's current JSON resource via the data.gouv.fr
    dataset API rather than a hardcoded resource URL — same reasoning as
    ``cnc_venues.py::fetch_cnc_active_establishments``.

    Args:
        client: Shared async HTTP client.

    Returns:
        Every establishment in the current JSON resource.
    """
    dataset_response = await client.get(_DATASET_API_URL)
    dataset_response.raise_for_status()
    resources = dataset_response.json()["resources"]
    json_url = next(
        resource["url"] for resource in resources if resource["format"] == "json"
    )
    payload_response = await client.get(json_url)
    payload_response.raise_for_status()
    return parse_idf_establishments(payload_response.json())
