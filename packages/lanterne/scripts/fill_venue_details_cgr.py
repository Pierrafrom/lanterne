"""One-off enrichment: address/website for CGR-network venues.

Every address/website below was confirmed via independent web search
results (CGR's own site, PagesJaunes, Acceslibre, tourism listings) —
``cgrcinemas.fr`` itself was not scraped directly. Two addresses already
in the database turned out to be wrong, not just imprecise:

- ``CGR Épinay-sur-Seine``: was "Impasse des Moineaux"; the real address
  is "5 Avenue Joffre, 93800 Épinay-sur-Seine".
- ``CGR Sarcelles - My Place``: was in "Groslay" — a different, nearby
  commune entirely; the real address is "2 contre-allée Henri Dunant,
  95200 Sarcelles".

Not a repeatable scraper — see
docs/decisions/0021-venue-screen-split-and-geocoding.md. Coordinates are
left to a follow-up ``lanterne geocode-venues`` run.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fill_venue_details_cgr.py
    uv run lanterne geocode-venues
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository._commit import commit

# venue id -> (address override or None to keep the existing one, website)
_DETAILS: dict[int, tuple[str | None, str]] = {
    212: (
        "5 Avenue Joffre, 93800 Épinay-sur-Seine",
        "https://www.cgrcinemas.fr/cinema/b0261-cgr-epinay-sur-seine/",
    ),
    213: (
        "317 Allée Haute de l'Agora, 91000 Évry-Courcouronnes",
        "https://www.cgrcinemas.fr/cinema/b0059-cgr-evry/",
    ),
    214: (
        "Place Henri Dunant, 78200 Mantes-la-Jolie",
        "https://www.cgrcinemas.fr/cinema/b0121-cgr-mantes-la-jolie/",
    ),
    123: (
        "2 contre-allée Henri Dunant, 95200 Sarcelles",
        "https://www.cgrcinemas.fr/cinema/p9520-cgr-sarcelles-my-place/",
    ),
    215: (
        None,
        "https://www.cgrcinemas.fr/cinema/b9114-cgr-torcy-marne-la-vallee/",
    ),
    124: (
        None,
        "https://www.cgrcinemas.fr/cinema/w9202-cgr-nanterre-coeur-universite/",
    ),
}


async def main() -> None:
    """Set address (when improved) and website for every CGR venue."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        updated = 0
        for venue_id, (address, website) in _DETAILS.items():
            venue = await session.get(Venue, venue_id)
            if venue is None:
                print(f"  venue {venue_id}: not found, skipping")
                continue
            if address is not None and venue.address != address:
                print(f"  '{venue.name}': address corrected to '{address}'")
                venue.address = address
                venue.location = None
            venue.website = website
            session.add(venue)
            updated += 1
        await commit(session)
        print(f"Updated {updated} CGR venue(s)")


if __name__ == "__main__":
    asyncio.run(main())
