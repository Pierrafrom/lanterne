"""The merged official-establishment shape, combining both CNC datasets.

``io/reference/cnc_venues.py`` and ``io/reference/idf_venues.py`` each parse
their own source into a source-shaped dataclass; :func:`merge_establishments`
combines them by ``cnc_id`` — the two datasets share the same id space
(confirmed live, e.g. id ``"55"`` is "ELYSEES LINCOLN" in both), so no fuzzy
matching is needed at this level. Fuzzy matching only happens one level up,
against existing scraped :class:`~lanterne.core.models.Venue` rows (see
``io/reference/matching.py``).
"""

from dataclasses import dataclass

from lanterne.io.reference.cnc_venues import CncEstablishment
from lanterne.io.reference.idf_venues import IdfEstablishment

# The CNC master list covers all of France; this project only covers
# Paris/IDF (see CLAUDE.md), so establishments are scoped to these
# départements — the first two characters of an INSEE commune code.
ILE_DE_FRANCE_DEPARTMENTS = frozenset({"75", "77", "78", "91", "92", "93", "94", "95"})


@dataclass(frozen=True, slots=True)
class OfficialEstablishment:
    """One cinema, per the CNC's official referential.

    Attributes:
        cnc_id: The CNC's own establishment authorization number.
        name: Establishment name, as the CNC master list spells it.
        dep: Département code (2 characters).
        insee_code: INSEE commune code.
        commune: Commune name.
        address: Street address, when the Île-de-France file has this
            establishment (the CNC master list carries none).
        screen_count: Total screens, when known.
        seat_count: Total seats, when known.
        art_et_essai: Whether the CNC classifies this venue as Art et
            Essai (arthouse).
        art_et_essai_grade: The CNC's Art et Essai grade (A through E),
            when the Île-de-France file has this establishment.
        art_et_essai_labels: Finer-grained Art et Essai labels (e.g.
            ``["PR"]``), when the Île-de-France file has this
            establishment.
        is_multiplex: Whether the CNC classifies this establishment as a
            multiplex, when the Île-de-France file has this establishment.
        latitude: Decimal-degrees latitude, when the Île-de-France file has
            this establishment.
        longitude: Decimal-degrees longitude, when the Île-de-France file
            has this establishment.
        is_active: Always ``True`` — every row in the CNC's "active
            establishments" list is, by definition, active.
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
    is_multiplex: bool | None
    latitude: float | None
    longitude: float | None
    is_active: bool = True


def merge_establishments(
    cnc_establishments: list[CncEstablishment],
    idf_establishments: list[IdfEstablishment],
) -> list[OfficialEstablishment]:
    """Merge the CNC master list with the Île-de-France complement, by id.

    The CNC master list is the base (it is the current, "active" census);
    the Île-de-France file only ever enriches an establishment already in
    that base — an establishment present in the older Île-de-France file
    but absent from the current CNC active list is treated as no longer
    active and is not surfaced (see
    ``docs/decisions/0026-official-reference-data-layer.md`` on the
    referential's staleness-by-design).

    Args:
        cnc_establishments: Every establishment from the CNC master list.
        idf_establishments: Every establishment from the Île-de-France file.

    Returns:
        One :class:`OfficialEstablishment` per Île-de-France establishment
        in the CNC master list, enriched with the Île-de-France file's
        fields when available.
    """
    idf_by_id = {
        establishment.cnc_id: establishment for establishment in idf_establishments
    }
    merged = []
    for cnc in cnc_establishments:
        if cnc.dep not in ILE_DE_FRANCE_DEPARTMENTS:
            continue
        idf = idf_by_id.get(cnc.cnc_id)
        merged.append(
            OfficialEstablishment(
                cnc_id=cnc.cnc_id,
                name=cnc.name,
                dep=cnc.dep,
                insee_code=cnc.insee_code,
                commune=cnc.commune,
                address=idf.address if idf is not None else None,
                screen_count=cnc.screen_count
                if cnc.screen_count is not None
                else (idf.screen_count if idf is not None else None),
                seat_count=cnc.seat_count
                if cnc.seat_count is not None
                else (idf.seat_count if idf is not None else None),
                art_et_essai=cnc.art_et_essai
                or (idf.art_et_essai if idf is not None else False),
                art_et_essai_grade=idf.art_et_essai_grade if idf is not None else None,
                art_et_essai_labels=idf.art_et_essai_labels
                if idf is not None
                else None,
                is_multiplex=idf.is_multiplex if idf is not None else None,
                latitude=idf.latitude if idf is not None else None,
                longitude=idf.longitude if idf is not None else None,
            )
        )
    return merged
