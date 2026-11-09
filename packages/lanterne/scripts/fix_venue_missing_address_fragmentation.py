"""One-off cleanup: fix every venue missing an address, and what caused it.

Investigated live (2026-07-20): every ``Venue`` row with ``address IS NULL``
turned out to be a fragment, not a genuinely address-less venue — the
physical cinema already existed as a separate, fully-enriched row, and each
address-less row was a scraper-wording variant that failed to resolve onto
it:

- ``Les Studios`` (id 1, ``CINEMATHEQUE`` source): a workshop-page screening
  scraped *before* ``cinematheque.py`` started passing ``known_venue`` to
  ``structure_via_llm`` (confirmed live: the page itself reads "Les Studios :
  stages et ateliers Jeune public", at 51 Rue de Bercy — La Cinémathèque
  française's own address). The current deployed code already forces
  ``known_venue`` on every Cinémathèque listing, so this cannot recur; it's a
  stale pre-fix row.
- ``Ciné-club Les Icônes de la Comédie Musicale`` (id 78), ``Ciné-bar Hôtel
  de Ville`` (id 79), ``Tournebride Bar`` (id 81), ``Epée de bois`` (id 57):
  Paris Ciné Info's ``get_showtimes.php`` reports a showtime's *venue* as
  ``showtime["title"]`` with no ``tid`` for these — confirmed live, this
  field carries the in-house ciné-club/bar name for a club-programmed
  screening rather than the physical cinema, and with no ``tid`` the row
  never gets to piggyback on ``VenueRepository.resolve``'s external-id
  matching (see ``test_missing_tid_leaves_venue_external_id_none``). Booking
  URLs confirm the real venue in each case (multicine.fr/parnassiens,
  luminor-hoteldeville.com, cine-epeedebois.fr) — see
  ``core/dedup.py``'s ``_VENUE_ALIASES`` for the matching aliases added
  alongside this cleanup so future showtimes under the same club name
  resolve directly instead of re-fragmenting.
- ``Lost in Frenchlation (english subs)`` (id 84): same missing-``tid``
  cause, but *not* given an alias — the club rotates across at least five
  independent Paris cinemas (Club de l'Étoile, Luminor, L'Entrepôt,
  Arlequin, L'Épée de Bois, and this Lucernaire date), so a static alias
  would silently mis-assign a future screening at a different venue. Fixed
  as a one-off merge only.

Two more address-collision duplicates surfaced by the same audit (not
missing-address, but the same root cause — a spelling/wording variant that
never matched the canonical slug):

- ``Luxy`` (id 210, from the ``OFFI`` width source, 47 screenings) vs
  ``Le Luxy`` (id 51, from ``PREMIERE_PROJO``, 8 screenings): identical
  address once the ``Luxy`` row's "Georges-Cosnat" typo is accounted for
  (the street is Avenue Georges *Gosnat*) — both geocoded to the exact same
  point. Kept ``Le Luxy`` (the grammatically correct display name) as
  canonical and added a ``luxy`` alias so ``OFFI``, the higher-volume
  source, stops re-fragmenting it.
- ``Écoles Cinéma Club`` (id 53, ``PREMIERE_PROJO``, 1 screening) vs
  ``Ecoles Cinema Club`` (id 299, ``PARIS_CINE_INFO``, 74 screenings): same
  address, accent-only variant — geocoded to the exact same point. Kept the
  higher-volume unaccented spelling.

See docs/decisions/0024-venue-data-quality-cleanup.md for the prior round
of the same class of audit.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fix_venue_missing_address_fragmentation.py
    uv run lanterne geocode-venues
"""

import asyncio

from sqlmodel.ext.asyncio.session import AsyncSession

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository import EventRepository
from lanterne.io.repository._commit import commit

# (winner id, loser id) — winner is the row that already carries (or, for the
# address-collision pair, best matches) the real, fully-enriched venue.
_MERGES: tuple[tuple[int, int], ...] = (
    (5, 1),  # La Cinémathèque française <- Les Studios
    (41, 78),  # Les 7 Parnassiens <- Ciné-club Les Icônes de la Comédie Musicale
    (56, 79),  # Luminor Hôtel de Ville <- Ciné-bar Hôtel de Ville
    (287, 81),  # L'Épée de Bois <- Tournebride Bar
    (287, 57),  # L'Épée de Bois <- Epée de bois
    (98, 84),  # Lucernaire <- Lost in Frenchlation (english subs)
    (51, 210),  # Le Luxy <- Luxy
    (299, 53),  # Ecoles Cinema Club <- Écoles Cinéma Club
)

# venue id -> corrected address. La Cinémathèque française's stored address
# was missing its house number; Les Cinoches Desnos was carrying its sister
# venue's address outright (both are managed by Grand Paris Sud, but they
# are two separate buildings — confirmed live against
# grandparissud.fr/equipement/les-cinoches-plateau and .../les-cinoches-rn7).
_ADDRESS_FIXES: dict[int, str] = {
    5: "51 Rue de Bercy, 75012 Paris",
    151: "3 Allée Jean Ferrat, 91130 Ris-Orangis",  # Cinoches Desnos
    152: "11 Place Jacques Brel, 91130 Ris-Orangis",  # Cinoches RN7
}


async def _merge_all(repo: EventRepository, session: AsyncSession) -> None:
    """Merge every duplicate pair, winner keeping every screening."""
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


async def _fix_addresses(session: AsyncSession) -> None:
    """Correct each venue's address and clear its now-stale geocoding."""
    for venue_id, address in _ADDRESS_FIXES.items():
        venue = await session.get(Venue, venue_id)
        if venue is None:
            print(f"  venue {venue_id}: not found, skipping")
            continue
        if venue.address != address:
            print(f"  '{venue.name}': address corrected to '{address}'")
            venue.address = address
            venue.location = None
            venue.geocode_source = None
            venue.needs_review = False
            session.add(venue)
    await commit(session)


async def main() -> None:
    """Merge every fragment, then correct the two collision addresses."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        repo = EventRepository(session)
        await _merge_all(repo, session)
        await _fix_addresses(session)
        print("Done")


if __name__ == "__main__":
    asyncio.run(main())
