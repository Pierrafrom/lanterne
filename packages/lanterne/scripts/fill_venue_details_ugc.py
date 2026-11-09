"""One-off enrichment: address/website for UGC-network venues, from ugc.fr.

Every address and website URL below was read directly off
``https://www.ugc.fr/cinemas.html`` (2026-07-17) — the chain's own official
cinema locator, which lists every venue with its street address. Not a
repeatable scraper: per
docs/decisions/0021-venue-screen-split-and-geocoding.md, venue details are
effectively static and researched once by hand, not re-fetched every
scrape run. Coordinates are deliberately left to a follow-up
``lanterne geocode-venues`` run rather than computed here — that command
already knows how to turn a verified address into a precise, GiST-indexed
``location`` (see docs/decisions/0022-postgres-extensions.md).

Also merges two duplicate pairs discovered while cross-referencing this
data against the database: ``UGC Ciné Cité Maillot`` (a 1-screening
fragment) into the already-correct ``UGC Maillot``, and ``UGC Le Central``
into ``Le Central`` (the same Gif-sur-Yvette venue, winner picked by
screening volume).

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fill_venue_details_ugc.py
    uv run lanterne geocode-venues   # after every fill_venue_details_*.py run
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository import EventRepository
from lanterne.io.repository._commit import commit

_BASE_URL = "https://www.ugc.fr/"

# venue id -> (address, website path)
_DETAILS: dict[int, tuple[str, str]] = {
    260: (
        "Centre Commercial Les Quatre Temps, 92092 Paris-La-Défense",
        "cinema-ugc-cine-cite-la-defense.html",
    ),
    266: (
        "8 Promenade Cœur de Ville, 92130 Issy-les-Moulineaux",
        "cinema-ugc-issy-les-moulineaux.html",
    ),
    264: (
        "Centre Commercial Rosny 2, 16 rue Conrad Adenauer, 93110 Rosny-sous-Bois",
        "cinema-ugc-cine-cite-rosny.html",
    ),
    262: (
        "Centre Commercial O'Parinor, Le Haut de Galy, 93600 Aulnay-sous-Bois",
        "cinema-ugc-cine-cite-o-parinor.html",
    ),
    259: (
        "Centre Commercial Créteil Soleil, 94000 Créteil",
        "cinema-ugc-cine-cite-creteil.html",
    ),
    261: (
        "Centre Commercial Arcades, 93193 Noisy-le-Grand",
        "cinema-ugc-cine-cite-noisy-le-grand.html",
    ),
    258: (
        "8 Place des 3 Gares, 95800 Cergy-le-Haut",
        "cinema-ugc-cine-cite-cergy-le-haut.html",
    ),
    267: (
        "38 rue du Général de Gaulle, 95880 Enghien-les-Bains",
        "cinema-ugc-enghien.html",
    ),
    269: (
        "Centre Commercial Régional Vélizy 2, 78140 Vélizy-Villacoublay",
        "cinema-ugc-cine-cite-velizy.html",
    ),
    263: (
        "2 Avenue Charles de Gaulle, 78150 Le Chesnay",
        "cinema-ugc-cine-cite-parly.html",
    ),
    265: (
        "1 Avenue de la Source de la Bièvre, 78180 Montigny-le-Bretonneux",
        "cinema-ugc-cine-cite-sqy-ouest.html",
    ),
    240: ("6 Rue Saint-Simon, 78000 Versailles", "cinema-ugc-roxane.html"),
    158: ("7 Rue Rameau, 78000 Versailles", "cinema-ugc-cyrano.html"),
    268: (
        "Centre Commercial Mon Grand Plaisir, 1170 Avenue de Saint-Germain, "
        "78370 Plaisir",
        "cinema-ugc-plaisir.html",
    ),
    190: ("Square de la Mairie, 91190 Gif-sur-Yvette", "cinema-le-central.html"),
    116: (
        "25-27-29 rue du Vieux Marché, 78100 Saint-Germain-en-Laye",
        "cinema-c2l-saint-germain.html",
    ),
    103: ("6 rue Hoche, 78500 Sartrouville", "cinema-le-cin-hoche.html"),
    115: ("112 Rue du Général de Gaulle, 78300 Poissy", "cinema-c2l-poissy.html"),
    211: ("11 Place Henri IV, 77100 Meaux", "cinema-ugc-le-majestic.html"),
    43: ("103 boulevard du Montparnasse, 75006 Paris", "cinema-ugc-rotonde.html"),
}

# (winner id, loser id)
_MERGES: tuple[tuple[int, int], ...] = (
    (278, 24),  # UGC Maillot <- UGC Ciné Cité Maillot
    (190, 120),  # Le Central <- UGC Le Central
)


async def main() -> None:
    """Merge duplicates, then set address/website for every UGC venue."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        repo = EventRepository(session)
        for winner_id, loser_id in _MERGES:
            winner = await session.get(Venue, winner_id)
            loser = await session.get(Venue, loser_id)
            if winner is None or loser is None:
                print(f"  {winner_id}/{loser_id}: one side not found, skipping")
                continue
            print(
                f"  merging '{loser.name}' (id={loser_id}) into "
                f"'{winner.name}' (id={winner_id})"
            )
            await repo.merge_venue(loser=loser, winner=winner)

        updated = 0
        for venue_id, (address, path) in _DETAILS.items():
            venue = await session.get(Venue, venue_id)
            if venue is None:
                print(f"  venue {venue_id}: not found, skipping")
                continue
            # Overwrite unconditionally, not just when null: every value here
            # was read directly off ugc.fr just now, higher-confidence than
            # whatever (possibly stale) value may already be stored — e.g.
            # UGC Le Majestic's stored address ("Rue de l'Arbalète") turned
            # out to be wrong; the site says "11 Place Henri IV".
            if venue.address != address:
                venue.address = address
                venue.location = None  # stale coordinates for the old address
            venue.website = _BASE_URL + path
            session.add(venue)
            updated += 1
        await commit(session)
        print(f"Updated {updated} UGC venue(s)")


if __name__ == "__main__":
    asyncio.run(main())
