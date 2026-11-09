"""One-off enrichment: address/website for Pathé-network venues.

Website URLs follow Pathé's confirmed, consistent slug pattern
(``https://www.pathe.fr/cinemas/cinema-pathe-<slug>``, verified against
several already-correct entries already in the database — e.g. Alésia,
Aquaboulevard, Wepler). ``pathe.fr`` blocks direct automated fetches
(403, even via a real browser — a "Allo Houston ?" bot-detection page),
so every address below was instead confirmed via independent web search
results (PagesJaunes, Offi, Cinéfil, tourism-office listings), not
guessed. Two addresses already in the database turned out to be wrong,
not just incomplete, and are corrected here:

- ``Pathé Levallois``: was "Rue Curnonsky, 75017 Paris" (Paris postal
  code for a Levallois-Perret venue — internally inconsistent); the real
  address is "28 rue d'Alsace, 92300 Levallois-Perret".
- ``Pathé Quai d'Ivry``: was "5 Rue François Mitterrand"; the real
  address is "2 quai Marcel-Boyer, 94200 Ivry-sur-Seine".

Not a repeatable scraper — see
docs/decisions/0021-venue-screen-split-and-geocoding.md. Coordinates are
left to a follow-up ``lanterne geocode-venues`` run.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fill_venue_details_pathe.py
    uv run lanterne geocode-venues
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository._commit import commit

_BASE_URL = "https://www.pathe.fr/cinemas/"

# venue id -> (address override or None to keep the existing one, slug)
_DETAILS: dict[int, tuple[str | None, str]] = {
    165: (None, "cinema-pathe-aeroville"),
    226: (None, "cinema-pathe-belle-epine"),
    227: (
        "101 boulevard Jean Jaurès, 92100 Boulogne-Billancourt",
        "cinema-pathe-boulogne",
    ),
    170: ("3 Allée du Préambule, 77127 Lieusaint", "cinema-pathe-carre-senart"),
    228: (None, "cinema-pathe-conflans"),
    229: (None, "cinema-pathe-dammarie"),
    231: (None, "cinema-pathe-massy"),
    230: ("28 rue d'Alsace, 92300 Levallois-Perret", "cinema-pathe-levallois"),
    28: ("2 quai Marcel-Boyer, 94200 Ivry-sur-Seine", "cinema-pathe-quai-d-ivry"),
    172: (None, "cinema-pathe-saint-denis"),
}


async def main() -> None:
    """Set address (when improved) and website for every Pathé venue."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        updated = 0
        for venue_id, (address, slug) in _DETAILS.items():
            venue = await session.get(Venue, venue_id)
            if venue is None:
                print(f"  venue {venue_id}: not found, skipping")
                continue
            if address is not None and venue.address != address:
                print(f"  '{venue.name}': address corrected to '{address}'")
                venue.address = address
                venue.location = None
            venue.website = _BASE_URL + slug
            session.add(venue)
            updated += 1
        await commit(session)
        print(f"Updated {updated} Pathé venue(s)")


if __name__ == "__main__":
    asyncio.run(main())
