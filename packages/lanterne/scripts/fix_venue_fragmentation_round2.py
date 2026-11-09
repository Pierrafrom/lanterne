"""One-off cleanup: merge venue rows found via a pg_trgm similarity audit.

Confirmed live (2026-07-17), via ``SELECT similarity(a.name, b.name) ...``
now that ``pg_trgm`` is available (see
docs/decisions/0022-postgres-extensions.md): five more name-variant
duplicate pairs, each confirmed either by an identical address or by
strong corroborating evidence (a stable ``paris_cine_info_tid``, source
mix, and screening volume). See docs/decisions/0024-venue-data-quality-cleanup.md.

``UGC Ciné Cité La Défense`` (260) is a special case: its address matched
``UGC Ciné Cité Cergy-le-Haut`` (258) exactly — a genuinely different,
also-real venue — because both share the same self-inflicted geocoding
contamination as ``fix_contaminated_venue_addresses.py`` fixed elsewhere,
just with an address that happens to carry a real Île-de-France postal
code (Cergy, 95) and so slipped past that script's postal-code filter.
Its address/location is cleared here too, alongside the merge.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fix_venue_fragmentation_round2.py
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository import EventRepository
from lanterne.io.repository._commit import commit

# (winner id, loser id) — winner picked by materially higher screening
# count and/or a confirmed paris_cine_info_tid.
_MERGES: tuple[tuple[int, int], ...] = (
    (279, 12),  # 7 Batignolles <- Les 7 Batignolles
    (288, 50),  # Christine Cinema Club <- Christine Cinéma Club
    (260, 17),  # UGC Ciné Cité La Défense <- UGC La Défense
    (43, 239),  # UGC Rotonde <- Rotonde
    (293, 59),  # CGR Paris Lilas <- CGR Paris - Lilas
)

# Winner whose own address is itself contaminated (see module docstring)
# and must be cleared, not kept, despite winning the merge on screening
# volume.
_ALSO_RESET_ADDRESS = (260,)


async def main() -> None:
    """Merge each duplicate pair, then clear any winner's contaminated address."""
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

        for venue_id in _ALSO_RESET_ADDRESS:
            venue = await session.get(Venue, venue_id)
            if venue is None:
                continue
            print(f"  clearing contaminated address on '{venue.name}' (id={venue_id})")
            venue.address = None
            venue.location = None
            venue.geocode_source = None
            venue.needs_review = False
            session.add(venue)
        await commit(session)
        print("Done")


if __name__ == "__main__":
    asyncio.run(main())
