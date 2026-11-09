"""One-off enrichment: address for remaining independent venues (77/78/91/92).

Continues ``fill_venue_details_independents_1.py`` through ``_3.py``, this
time for venues that don't appear on offi.fr's own department listing
pages (either paginated past what was crawled, or simply outside the
department-by-department browse used so far) and were instead located via
web search, then verified on the venue's own offi.fr page the same way as
every prior script in this series — exact address text plus, where the
name is generic enough to collide with another venue (``Variétés``,
``Ariel - Centre``, ``Cinéma Jean-Gabin``), a check against every
same-named offi.fr candidate to confirm the right one.

``Cinéma Jean-Gabin`` (id 245) is a particular case: Argenteuil's
``Le Figuier Blanc`` culture centre informally brands one of its rooms
"cinéma Jean-Gabin" too, but that's not a distinct offi.fr listing —
offi.fr's actual "Cinéma Jean-Gabin" entry is a separate municipal cinema
in Champagne-sur-Seine (Seine-et-Marne), and its name matches this DB
row exactly (down to the "Cinéma" prefix), unlike the Figuier Blanc
room. Not conclusive from shared screenings (both play wide-release
titles), but the exact name match is strong enough on its own.

Not a repeatable scraper — see
docs/decisions/0021-venue-screen-split-and-geocoding.md. Coordinates are
left to a follow-up ``lanterne geocode-venues`` run.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fill_venue_details_independents_4.py
    uv run lanterne geocode-venues
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository._commit import commit

# venue id -> address
_DETAILS: dict[int, str] = {
    236: "35 rue Bournard, 92700 Colombes",
    118: "3 rue Ledru-Rollin, 92150 Suresnes",
    238: "rue du Général Delort, 77160 Provins",
    161: "6 rue de France, 77300 Fontainebleau",
    209: "49 rue Maurice-Thorez, 92000 Nanterre",
    271: "4 rue de l'Éperon, 77000 Melun",
    111: "99 avenue Paul-Doumer, 92500 Rueil-Malmaison",
    133: "Centre Commercial des 7 Mares, 78990 Élancourt",
    110: "15 place Léon-Cassé, 91100 Corbeil-Essonnes",
    222: "72 boulevard de la République, 92420 Vaucresson",
    173: "1 Place Jane Rhodes, 92350 Le Plessis-Robinson",
    247: "1 rue Pierre et Marie-Curie, 92230 Gennevilliers",
    199: "4 rue de Chézy, 92200 Neuilly-sur-Seine",
    129: "13 Rue du Camp Romain, 91490 Milly-la-Forêt",
    245: "Palais des Rencontres, 77430 Champagne-sur-Seine",
    35: "10 avenue de la Division Leclerc, 92160 Antony",
    274: "82 rue Marcel Bourdarias, 94140 Alfortville",
}


async def main() -> None:
    """Set address for each venue above (also clears stale coordinates)."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        updated = 0
        for venue_id, address in _DETAILS.items():
            venue = await session.get(Venue, venue_id)
            if venue is None:
                print(f"  venue {venue_id}: not found, skipping")
                continue
            if venue.address != address:
                if venue.address is not None:
                    print(f"  '{venue.name}': address corrected to '{address}'")
                venue.address = address
                venue.location = None
                venue.geocode_source = None
                venue.needs_review = False
                session.add(venue)
                updated += 1
        await commit(session)
        print(f"Updated {updated} venue(s)")


if __name__ == "__main__":
    asyncio.run(main())
