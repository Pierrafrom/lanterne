"""One-off enrichment: address for independent venues (93/92/94 departments).

Every address below was read directly off the venue's own page on
offi.fr — the exact source this project's ``OffiScraper`` already
scrapes for its screenings (see ``io/scrapers/offi.py``), browsed
department by department (``/cinema/<department-slug>.html``) since
general web search was unavailable this session (rate-limited, and every
major search engine blocks automated Playwright access with a
CAPTCHA/403). Three addresses already in the database turned out to be
wrong — all sharing the same failure shape as
``docs/decisions/0024-venue-data-quality-cleanup.md``'s contamination
finding (a plausible-looking but wrong address, in this case surviving
the postal-code-based cleanup because the wrong postal code still
happened to be a real Île-de-France one):

- ``Cinéma Alice Guy``: was "93100 Montreuil" — the real venue is in
  Bobigny (93000).
- ``Le Vincennes``: was "Cours de Vincennes, 75020 Paris" — the real
  address is "30 avenue de Paris, 94300 Vincennes".
- ``Luxy``: was "Avenue de la Division Leclerc, 93350 Le Bourget" — the
  real address is "77 avenue Georges-Cosnat, 94200 Ivry-sur-Seine".

Not a repeatable scraper — see
docs/decisions/0021-venue-screen-split-and-geocoding.md. Coordinates are
left to a follow-up ``lanterne geocode-venues`` run.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fill_venue_details_independents_2.py
    uv run lanterne geocode-venues
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository._commit import commit

# venue id -> address
_DETAILS: dict[int, str] = {
    166: "21 avenue Daniel Perdrigé, 93360 Neuilly-Plaisance",
    207: "16 mail Debré-Berhan, place Gabriel Péri, 93150 Le Blanc-Mesnil",
    272: "36 rue Eugène-Massé, 93190 Livry-Gargan",
    107: "10 avenue Francis-de-Pressensé, 93350 Le Bourget",
    135: "24 rue Aretha Franklin, 93000 Bobigny",
    178: "29 bis avenue du Général-de-Gaulle, 93290 Tremblay-en-France",
    189: "4 place de la Libération, 93160 Noisy-le-Grand",
    144: "28 avenue André-Morizet, 92100 Boulogne-Billancourt",
    187: "184 bis boulevard Saint-Denis, 92400 Courbevoie",
    192: "Place Charles de Gaulle, 92410 Ville-d'Avray",
    246: "7 Villa des Aubépines, 92270 Bois-Colombes",
    249: "17 rue Béranger, 92240 Malakoff",
    109: "1 place du Fer-à-Cheval, 94310 Orly",
    119: "13 rue Guillaume-Budé, 94350 Villiers-sur-Marne",
    142: "40 allée Parmentier, Centre Commercial, 94000 Créteil",
    162: "1 rue Paul-Signac, 94110 Arcueil",
    181: "243 ter avenue de la République, 94120 Fontenay-sous-Bois",
    185: "14 rue Dispan, 94240 L'Haÿ-les-Roses",
    37: "30 avenue de Paris, 94300 Vincennes",
    206: "70 avenue de la République, 94100 Saint-Maur-des-Fossés",
    210: "77 avenue Georges-Cosnat, 94200 Ivry-sur-Seine",
    235: "12 avenue Cousin-de-Méricourt, 94230 Cachan",
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
                session.add(venue)
                updated += 1
        await commit(session)
        print(f"Updated {updated} venue(s)")


if __name__ == "__main__":
    asyncio.run(main())
