"""One-off cleanup: collapse MK2's fragmented Venue rows onto their real tid.

The venue-name-fragmentation bug (see ``docs/decisions/0012-retire-lechampo.md``
and ``docs/coverage-matrix.md``) left 17 ``Venue`` rows for MK2's 11 physical
rooms, created before ``Venue.paris_cine_info_tid`` existed. The tid-based
merge in ``EventRepository._resolve_venue`` only fires for a *new* screening,
so it self-heals lazily over future scrapes — this script does the same
merge immediately, using the live tid mapping confirmed against
paris-cine.info (2026-07-14), rather than waiting for next week's cron to
happen to see a genuinely new showtime at each fragmented room.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python scripts/fix_mk2_venue_fragmentation.py
"""

import asyncio

from sqlmodel import select

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository import EventRepository

# Confirmed live against paris-cine.info's get_showtimes.php on 2026-07-14
# (see the room->tid discovery in this session's conversation): every
# MK2 venue name currently reported, mapped to its stable theatre id, plus
# every wording variant found among the 17 stored Venue rows that resolves
# to the same physical room.
_TID_GROUPS: dict[str, tuple[str, list[str]]] = {
    "C0140": (
        "MK2 Bastille (Beaumarchais)",
        [
            "MK2 Bastille (Beaumarchais)",
            "MK2 Bastille (côté Beaumarchais)",
            "mk2 bastille beaumarchais",
        ],
    ),
    "C0040": (
        "MK2 Bastille (Fg St Antoine)",
        ["MK2 Bastille (côté Fg St Antoine)", "mk2 bastille st-antoine"],
    ),
    "C0050": ("MK2 Beaubourg", ["MK2 Beaubourg"]),
    "C2954": (
        "MK2 Bibliothèque",
        [
            "MK2 Bibliothèque",
            "MK2 Bibliothèque Patouille et Momo, les contes de la forêt",
        ],
    ),
    "C0192": ("MK2 Gambetta", ["MK2 Gambetta"]),
    "C0144": ("MK2 Nation", ["MK2 Nation"]),
    "C0097": (
        "MK2 Odéon (St Germain)",
        ["MK2 Odéon (St Germain)", "MK2 Odéon (côté St Germain)"],
    ),
    "C0092": (
        "MK2 Odéon (St Michel)",
        ["MK2 Odéon (St Michel)", "MK2 Odéon (côté St Michel)"],
    ),
    "C0099": ("MK2 Parnasse", ["mk2 parnasse"]),
    "C1621": ("MK2 Quai de Loire", ["MK2 Quai de Loire"]),
    "C0003": ("MK2 Quai de Seine", ["MK2 Quai de Seine"]),
}


async def _fix_one_group(
    repo: EventRepository, tid: str, canonical_name: str, variant_names: list[str]
) -> None:
    """Merge every variant-named row in one tid group onto a single canonical row."""
    venues_by_name: dict[str, Venue] = {}
    for name in variant_names:
        venue = await repo._find_venue(name)  # noqa: SLF001 — one-off script, not app code
        if venue is not None:
            venues_by_name[name] = venue
    if not venues_by_name:
        print(f"  {tid} ({canonical_name}): no matching rows found, skipping")
        return

    winner = venues_by_name.get(canonical_name) or next(iter(venues_by_name.values()))
    for name, loser in venues_by_name.items():
        if loser.id == winner.id:
            continue
        print(
            f"  {tid}: merging '{name}' (id={loser.id}) into "
            f"'{winner.name}' (id={winner.id})"
        )
        await repo.merge_venue(loser=loser, winner=winner)

    winner.name = canonical_name
    winner.paris_cine_info_tid = tid
    repo._session.add(winner)  # noqa: SLF001 — one-off script, not app code
    await repo._session.commit()  # noqa: SLF001
    print(f"  {tid}: canonical row is now '{canonical_name}' (id={winner.id})")


async def main() -> None:
    """Merge every known MK2 wording variant onto its tid-canonical Venue row."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        repo = EventRepository(session)
        mk2_statement = select(Venue).where(Venue.name.ilike("%mk2%"))
        before = (await session.exec(mk2_statement)).all()
        print(f"MK2 venue rows before: {len(before)}")

        for tid, (canonical_name, variant_names) in _TID_GROUPS.items():
            await _fix_one_group(repo, tid, canonical_name, variant_names)

        after = (await session.exec(mk2_statement)).all()
        print(f"MK2 venue rows after: {len(after)}")
        for venue in sorted(after, key=lambda v: v.name):
            print(f"  - {venue.name} | tid={venue.paris_cine_info_tid}")


if __name__ == "__main__":
    asyncio.run(main())
