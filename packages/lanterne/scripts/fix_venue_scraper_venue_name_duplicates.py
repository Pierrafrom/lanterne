"""One-off cleanup: merge venue rows created by the scraper venue-name bug.

Two duplicate pairs found while completing the address-enrichment pass
(``fill_venue_details_independents_*.py``):

- ``Studio 28`` <- ``Cinéma Studio 28``: an address-formatting variant, same
  class of bug as ``fix_venue_address_collision_duplicates.py``.
- ``Fondation Jérôme Seydoux-Pathé`` <- ``Cinémathèque Pathé``: the LLM
  extraction bug fixed in ``io/scrapers/base.py``'s ``structure_via_llm``
  (see the ``known_venue`` parameter and
  docs/decisions/0024-venue-data-quality-cleanup.md) — the loser row here
  held *future* screenings (Sept/Oct 2026), confirming the bug was still
  live at the time of the previous merge in
  ``fix_venue_address_collision_duplicates.py`` and kept re-fragmenting
  this venue on every scrape until the scraper itself was fixed.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fix_venue_scraper_venue_name_duplicates.py
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository import EventRepository

# (winner id, loser id) — winner picked by materially higher screening count.
_MERGES: tuple[tuple[int, int], ...] = (
    (286, 48),  # Studio 28 <- Cinéma Studio 28
    (68, 99),  # Fondation Jérôme Seydoux-Pathé <- Cinémathèque Pathé
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
