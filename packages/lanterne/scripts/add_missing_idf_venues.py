"""One-off: add IDF cinemas confirmed real but absent from every source we scrape.

Investigated live (2026-07-21), prompted by a specific report that "Les Yeux
d'Elsa" (Saint-Cyr-l'École) was missing: cross-referencing offi.fr's full
7-department venue directory (166 venues discovered by walking every listing
page's pagination, see ``docs/coverage-matrix.md``) against this project's
``Venue`` table found 4 more offi.fr-listed cinemas with no row at all
(``Chaplin`` Mantes-la-Jolie, ``Méliès`` Nemours, ``Rotonde`` Étampes,
``Cinéma Jacques Prévert`` Gonesse — each confirmed live to be a distinct
building from every same/similar-named row already stored, not a duplicate).

"Les Yeux d'Elsa" itself is *not* on offi.fr's current directory pages at
all, despite having its own offi.fr page (confirmed live: the town's own
site announces it "reopened its doors", suggesting a period of closure that
dropped it from the department listing) — a reminder that offi.fr's listing
pages are themselves not a guaranteed-exhaustive census, only the best
single source found so far. A full IDF venue census would need a better
primary source (e.g. the CNC's official cinema registry) to close that gap
with confidence; this script only adds what's been individually confirmed.

None of these 5 have any scraper covering their programme yet — added here
as bare rows (name, address, kind) with no ``Screen``/``ScreeningEvent``, so
they at least appear on any venue-level map or listing. See
``docs/coverage-matrix.md`` for how/whether to wire up scraping later.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/add_missing_idf_venues.py
    uv run lanterne geocode-venues
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.dedup import canonicalize_venue_name, normalize_text
from lanterne.core.models import Venue
from lanterne.core.venues import classify_venue_kind
from lanterne.io.db import Database
from lanterne.io.repository._commit import commit
from lanterne.io.repository.venue_repository import VenueRepository

# name -> address, each confirmed live against the venue's own offi.fr page
# (or, for Les Yeux d'Elsa, its town's official site — see module docstring).
_NEW_VENUES: dict[str, str] = {
    "Chaplin": "Place Pierre-Mendès-France, 78200 Mantes-la-Jolie",
    "Méliès": "9 rue Bezout, 77140 Nemours",
    "Rotonde": "102 rue Saint-Jacques, 91150 Étampes",
    "Cinéma Jacques Prévert": "Place Aimé Césaire - La Fauconnière, 95500 Gonesse",
    "Les Yeux d'Elsa": "11 bis avenue Jean-Jaurès, 78210 Saint-Cyr-l'École",
}


async def main() -> None:
    """Insert each venue not already present under its canonical slug."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        repo = VenueRepository(session)
        for name, address in _NEW_VENUES.items():
            existing = await repo._find_venue(name)  # noqa: SLF001 — one-off script
            if existing is not None:
                print(f"  '{name}': already present (id={existing.id}), skipping")
                continue
            canonical = canonicalize_venue_name(name)
            venue = Venue(
                slug=normalize_text(canonical),
                name=canonical,
                kind=classify_venue_kind(canonical),
                address=address,
            )
            session.add(venue)
            print(f"  adding '{canonical}' — {address}")
        await commit(session)
        print("Done")


if __name__ == "__main__":
    asyncio.run(main())
