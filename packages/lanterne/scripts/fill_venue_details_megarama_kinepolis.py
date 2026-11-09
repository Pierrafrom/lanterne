"""One-off enrichment: address/website for Mégarama/Kinépolis/Gaumont venues.

Every address/website below was confirmed via independent web search
results. One address already in the database turned out to be wrong:
``Megarama Cormeilles-en-Parisis`` was "Rue Georges Méliès" — the real
address is "1 boulevard du Parisis, 95240 Cormeilles-en-Parisis".

Not a repeatable scraper — see
docs/decisions/0021-venue-screen-split-and-geocoding.md. Coordinates are
left to a follow-up ``lanterne geocode-venues`` run.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fill_venue_details_megarama_kinepolis.py
    uv run lanterne geocode-venues
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository._commit import commit

# venue id -> (address override or None to keep the existing one, website)
_DETAILS: dict[int, tuple[str | None, str]] = {
    217: (None, "https://arcueil.megarama.fr/"),
    218: (
        "1 boulevard du Parisis, 95240 Cormeilles-en-Parisis",
        "https://cormeilles.megarama.fr/",
    ),
    219: ("5 Avenue de l'Aubrac, 91940 Les Ulis", "https://les-ulis.megarama.fr/"),
    220: (None, "https://montigny.megarama.fr/"),
    216: (None, "https://villeneuve.megarama.fr/"),
    146: (None, "https://pince-vent.megarama.fr/"),
    253: (
        "66 rue Jean-Jaurès, 94500 Champigny-sur-Marne",
        "https://studio66.megarama.fr/",
    ),
    179: (None, "https://kinepolis.fr/cinemas/kinepolis-bretigny-sur-orge/infos/"),
    180: (None, "https://kinepolis.fr/cinemas/kinepolis-servon/info/"),
    171: (
        "1 esplanade François Truffaut, 77700 Chessy",
        "https://www.pathe.fr/cinemas/cinema-gaumont-disney-village",
    ),
}


async def main() -> None:
    """Set address (when improved) and website for every venue above."""
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
        print(f"Updated {updated} venue(s)")


if __name__ == "__main__":
    asyncio.run(main())
