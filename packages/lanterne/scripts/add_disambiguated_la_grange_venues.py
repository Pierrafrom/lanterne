"""One-off: add the 3 real "La Grange" cinemas, pre-stamped with their offi_slug.

Companion to the ``VenueRepository.resolve``/``offi.py`` homonym fix (see
their docstrings, 2026-07-22): the CNC registry confirms **three** distinct
"La Grange" cinemas in Île-de-France — Roissy-en-Brie (77), Vaux-le-Pénil
(77), and Bois-d'Arcy (78, officially "La Grange de la Tremblaye", which
avoids the name collision on its own). The existing ``Venue`` row (id 139,
slug "la grange") predates the fix and conflates whichever of these offi.fr
ever reported a showtime under the plain text "La Grange" — left alone
deliberately (see the module docstring reasoning below), since it only holds
ordinary width-source screenings that age out on their own via ``prune-db``
(14-day retention).

Each new row is created here with its ``offi_slug`` already stamped
(confirmed live against the venue's own offi.fr page, where one exists), so
the *next* real ``scrape`` run resolves straight to the correct row via
:meth:`VenueRepository.find_venue_by_external_id` — the tid-match path is
checked before any name-based ``slug`` match, so pre-stamping here is what
actually prevents old venue 139 from absorbing new screenings going forward,
not just a label. Roissy-en-Brie and Bois-d'Arcy each have a live offi.fr
page; Vaux-le-Pénil's cinema (confirmed real via its own booking site,
cineode.fr) has none, mirroring "Les Yeux d'Elsa" in
``add_missing_idf_venues.py`` — no scraper covers it yet.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/add_disambiguated_la_grange_venues.py
    uv run lanterne geocode-venues
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.dedup import normalize_text
from lanterne.core.models import Venue, VenueKind
from lanterne.io.db import Database
from lanterne.io.repository._commit import commit
from lanterne.io.repository.venue_repository import VenueRepository

# name -> (address, offi.fr venue-page slug or None), each confirmed live —
# see this module's docstring.
_NEW_VENUES: dict[str, tuple[str, str | None]] = {
    "La Grange (Roissy-en-Brie)": (
        "Ferme de l'Ayau, 77390 Roissy-en-Brie",
        "la-grange-2412",
    ),
    "La Grange de la Tremblaye": (
        "Rue du Parc, Ferme de la Tremblaye, 78390 Bois-d'Arcy",
        "cinema-de-la-grange-5881",
    ),
    "La Grange (Vaux-le-Pénil)": (
        "La Ferme des Jeux, Rue Ambroise Pro, 77000 Vaux-le-Pénil",
        None,
    ),
}


async def main() -> None:
    """Insert each disambiguated "La Grange" venue not already present."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        repo = VenueRepository(session)
        for name, (address, offi_slug) in _NEW_VENUES.items():
            existing = await repo._find_venue(name)  # noqa: SLF001 — one-off script
            if existing is not None:
                print(f"  '{name}': already present (id={existing.id}), skipping")
                continue
            venue = Venue(
                slug=normalize_text(name),
                name=name,
                kind=VenueKind.INDEPENDENT,
                address=address,
                offi_slug=offi_slug,
            )
            session.add(venue)
            print(f"  adding '{name}' — {address} (offi_slug={offi_slug})")
        await commit(session)
        print("Done")


if __name__ == "__main__":
    asyncio.run(main())
