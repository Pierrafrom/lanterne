"""One-off enrichment: address for independent venues (77/78/91/95 departments).

Every address below was read directly off the venue's own page on offi.fr
— the exact source this project's ``OffiScraper`` already scrapes for its
screenings (see ``io/scrapers/offi.py``), browsed department by department
(``/cinema/<department-slug>.html``) since general web search was
unavailable this session. Continues the work of
``fill_venue_details_independents_1.py`` and ``_2.py`` for the remaining
four departments (Seine-et-Marne, Yvelines, Essonne, Val-d'Oise).

Several existing addresses turned out to be wrong, split into two distinct
failure shapes:

- Geocoding contamination (``geocode_source=NAME_SEARCH``,
  ``needs_review=true``): the same self-inflicted bug documented in
  ``docs/decisions/0024-venue-data-quality-cleanup.md`` — ``Apollo``,
  ``Conti``, and ``La Rotonde (Sénart)`` had all been overwritten with an
  unrelated Paris/Versailles address guessed by a name-only geocode.
- Wrong data at scrape time (``geocode_source=ADDRESS``, not flagged for
  review): ``Cyrano`` held Versailles' address, but its screenings'
  film lineup (verified against offi.fr's own listings) matches
  Montgeron's ``Cyrano``, a distinct venue from ``UGC Cyrano`` — a merge
  of the two was planned before this check and is *not* being done.
  ``Henri-Langlois`` held a bare, truncated "75013 Paris". ``Le Cube
  Garges`` was missing its house number. ``Le Figuier Blanc`` held a
  street name that doesn't match offi.fr's.

Three ambiguous cases (multiple offi.fr venues sharing a generic name)
were disambiguated by cross-referencing each DB venue's actual scheduled
films against offi.fr's own per-venue listings, rather than guessed:

- ``Jacques-Prévert`` (id 177) -> confirmed as the Les Ulis venue (shared
  screenings of "Shana" and "Ulysse"), not the separate Gonesse venue of
  the same name (which has no corresponding row in this database yet).
- ``Cinoches Desnos`` (id 151) -> confirmed as offi.fr's "Cinoches RN7"
  in Ris-Orangis (shared screenings of "La Chaleur" and "Les Caprices de
  l'enfant Roi"), despite the display-name mismatch.
- ``Trianon`` (id 256) -> confirmed as the Sceaux venue (shared screening
  of "Le Vertige"), not the separate Romainville venue of the same name.

Not a repeatable scraper — see
docs/decisions/0021-venue-screen-split-and-geocoding.md. Coordinates are
left to a follow-up ``lanterne geocode-venues`` run.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fill_venue_details_independents_3.py
    uv run lanterne geocode-venues
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository._commit import commit

# venue id -> address
_DETAILS: dict[int, str] = {
    # Seine-et-Marne (77)
    164: "4 place du Miroir d'eau Plessis-le-Roi, 77176 Savigny-le-Temple",
    182: "Cour Émile-Zola, 77370 Nangis",
    184: "Place du 14 Juillet Sénart, 77550 Moissy-Cramayel",
    193: "4 avenue des Bosquets, 77290 Mitry-Mory",
    197: "16 rue de l'Eglise, 77480 Bray-sur-Seine",
    252: "31 Place d'Ariane, Centre urbain Chessy Val d'Europe, 77700 Chessy",
    108: "62 avenue de la République, 77340 Pontault-Combault",
    # Yvelines (78)
    113: "48 rue du Prieuré, 78600 Maisons-Laffitte",
    156: "Place Jean-Moulin, 78730 Saint-Arnoult-en-Yvelines",
    167: "Place de la Gare, 78160 Marly-le-Roi",
    176: "4 rue de la Mare-aux-Carats, 78180 Montigny-le-Bretonneux",
    203: "Place Henri-Dunant, 78580 Maule",
    233: "1 allée Henri-Langlois, 78340 Les Clayes-sous-Bois",
    # Essonne (91)
    105: "37-39 Grande-Rue, 91260 Juvisy-sur-Orge",
    114: "Salle Montaru, Parc des Célestins, 91460 Marcoussis",
    134: "2 rue des Colombes, ZAC de l'Aunaie, 91610 Ballancourt-sur-Essonne",
    145: "2 rue Marc Sangnier, 91330 Yerres",
    177: "avenue du Berry, 91940 Les Ulis",
    188: "1 avenue d'Alsace-Lorraine, 91550 Paray-Vieille-Poste",
    225: "Place de l'Hôtel-de-Ville, 91410 Dourdan",
    248: "4 rue Samuel Desbordes, 91200 Athis-Mons",
    151: "Place Jacques-Brel, 91130 Ris-Orangis",
    157: "112 avenue de la République, 91230 Montgeron",
    # Val-d'Oise (95)
    154: "place du Patis, 95290 L'Isle-Adam",
    159: "162 rue Maurice-Berteaux, 95870 Bezons",
    175: "rue de la Station, 95130 Franconville",
    186: "1 place du Cœur Battant, 95490 Vauréal",
    194: "40 avenue du Général de Gaulle, 95140 Garges-lès-Gonesse",
    195: "16-18 rue Grégoire-Collas, 95100 Argenteuil",
    204: "Centre Commercial le Forum, 95210 Saint-Gratien",
    223: "6 avenue Anatole-France, 95260 Beaumont-sur-Oise",
    251: "207 rue de Paris, 95150 Taverny",
    # Hauts-de-Seine (92) — "Trianon" disambiguation shares the same
    # generic-name pattern as the Essonne/Val-d'Oise cases above.
    256: "3 bis rue Marguerite Renaudin, 92330 Sceaux",
}


async def main() -> None:
    """Set address for each venue above (also clears stale geocoding)."""
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
