"""One-off cleanup: merge venue rows discovered as duplicates by address collision.

Confirmed live (2026-07-17), via a `GROUP BY address HAVING count(*) > 1`
audit: four venue-name pairs share the exact same street address, each a
scraper-wording variant of the same physical cinema (same class of bug as
``fix_mk2_venue_fragmentation.py``/``fix_cinematheque_fragmentation.py``,
just never caught before because neither wording was an already-known
alias). The winner in each pair is the row with materially more
screenings (the name a majority of sources settled on). See
docs/decisions/0024-venue-data-quality-cleanup.md for the full audit.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fix_venue_address_collision_duplicates.py
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository import EventRepository

# (winner id, loser id) — winner picked by materially higher screening count.
_MERGES: tuple[tuple[int, int], ...] = (
    (95, 125),  # Chaplin Denfert <- Chaplin
    (68, 304),  # Fondation Jérôme Seydoux-Pathé <- Cinémathèque Pathé, ...
    (147, 96),  # CinéPal' <- Cinepal' Palaiseau
    (255, 243),  # Théâtre et cinéma Jacques-Prévert <- Cinéma Jacques Prévert
)


async def main() -> None:
    """Merge each duplicate pair, winner keeping every screening."""
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
        print("Done")


if __name__ == "__main__":
    asyncio.run(main())
