"""One-off cleanup: collapse La Cinémathèque française's per-room Venue rows.

Confirmed live against the stored data (2026-07-16): "La Cinémathèque
française" existed as three separate ``Venue`` rows — the generic name plus
one row each for "Salle Georges Franju" and "Salle Henri Langlois" — even
though they are the same physical building. This predates the
:class:`~lanterne.core.models.Screen` split
(``docs/decisions/0021-venue-screen-split-and-geocoding.md``): a room name
should be a ``Screen`` under one ``Venue``, not its own ``Venue``.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fix_cinematheque_fragmentation.py
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Screen
from lanterne.io.db import Database
from lanterne.io.repository import EventRepository

_CANONICAL_NAME = "La Cinémathèque française"
_ROOM_NAMES = (
    "La Cinémathèque française - Salle Georges Franju",
    "La Cinémathèque française - Salle Henri Langlois",
)


async def main() -> None:
    """Merge the two per-room Venue rows into Screens under the canonical row."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        repo = EventRepository(session)
        winner = await repo._venues._find_venue(_CANONICAL_NAME)  # noqa: SLF001 — one-off script
        if winner is None:
            print(f"'{_CANONICAL_NAME}' not found, aborting")
            return

        for room_name in _ROOM_NAMES:
            loser = await repo._venues._find_venue(room_name)  # noqa: SLF001
            if loser is None:
                print(f"  '{room_name}': no matching row found, skipping")
                continue

            label = room_name.removeprefix(f"{_CANONICAL_NAME} - ")
            session.add(Screen(venue_id=winner.id, name=label))
            print(f"  created Screen '{label}' under '{winner.name}' (id={winner.id})")

            print(f"  merging '{room_name}' (id={loser.id}) into id={winner.id}")
            await repo.merge_venue(loser=loser, winner=winner)

        print(f"Done — canonical venue is '{winner.name}' (id={winner.id})")


if __name__ == "__main__":
    asyncio.run(main())
