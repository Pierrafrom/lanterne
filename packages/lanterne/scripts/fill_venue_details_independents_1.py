"""One-off enrichment: address/website for independent Île-de-France venues (batch 1).

Every address/website below was confirmed via web search results. Also
merges ``Méliès`` (a 69-screening fragment) into ``Le Méliès`` — the same
Montreuil art house cinema under two name variants.

Not a repeatable scraper — see
docs/decisions/0021-venue-screen-split-and-geocoding.md. Coordinates are
left to a follow-up ``lanterne geocode-venues`` run.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fill_venue_details_independents_1.py
    uv run lanterne geocode-venues
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository import EventRepository
from lanterne.io.repository._commit import commit

# venue id -> (address, website or None when unknown)
_DETAILS: dict[int, tuple[str, str | None]] = {
    137: (
        "Espace commercial du Bréau, RD 606, 77130 Varennes-sur-Seine",
        "https://www.cinema-confluences.com/varennes/",
    ),
    23: (
        "12 Place Jean Jaurès, 93100 Montreuil",
        "https://meliesmontreuil.fr/",
    ),
    106: (
        "1 Rue de la Station, 92600 Asnières-sur-Seine",
        "https://www.cinealcazar.fr/",
    ),
    174: (
        "23 rue Marcel Clavier, 77120 Coulommiers",
        "https://www.cinemacoulommiers.fr/",
    ),
}

# (winner id, loser id)
_MERGES: tuple[tuple[int, int], ...] = ((23, 305),)  # Le Méliès <- Méliès


async def main() -> None:
    """Merge duplicates, then set address/website for each venue above."""
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
        for venue_id, (address, website) in _DETAILS.items():
            venue = await session.get(Venue, venue_id)
            if venue is None:
                print(f"  venue {venue_id}: not found, skipping")
                continue
            if venue.address != address:
                venue.address = address
                venue.location = None
            if website is not None:
                venue.website = website
            session.add(venue)
            updated += 1
        await commit(session)
        print(f"Updated {updated} venue(s)")


if __name__ == "__main__":
    asyncio.run(main())
