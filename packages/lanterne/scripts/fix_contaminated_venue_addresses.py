"""One-off cleanup: clear venue addresses contaminated by a geocoding bug.

Confirmed live (2026-07-17): 87 venues ended up with an address entirely
outside Île-de-France (Berlin, São Paulo, Karachi, Trondheim, Rome,
Venice, London, several unrelated small French towns...), none of them
carrying a ``paris_cine_info_tid`` (the only source that legitimately
scrapes an address at all). Root cause, fully traced: an earlier session
step reset ``latitude``/``longitude``/``geocode_source``/``needs_review``
back to NULL for every ``NAME_SEARCH``-sourced venue (to re-run geocoding
with a newly added Île-de-France bounding box), but did not also reset
``address`` — so a wrong tier-2 (name-search) guess that had already been
backfilled into ``venue.address`` survived the reset. On the next
``geocode-venues`` run, that now-non-null (but wrong) address made tier 1
(direct address lookup) run instead of tier 2 — and tier 1 always sets
``needs_review=False``, since a direct address lookup is normally precise
by construction. The wrong guess was thus laundered into a falsely
"confirmed" result. ``VenueRepository.save_geocoding`` no longer backfills
``address`` from an unconfirmed (``needs_review=True``) match, closing
this hole for future runs — see
docs/decisions/0024-venue-data-quality-cleanup.md.

This script only clears the contaminated fields so the normal
``geocode-venues`` command can re-resolve them cleanly; it does not
attempt to guess a replacement address itself.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fix_contaminated_venue_addresses.py
"""

import asyncio

from sqlalchemy import text

from lanterne.config import Settings
from lanterne.io.db import Database
from lanterne.io.repository._commit import commit

# Matches a French IDF department postal code (75/77/78/91/92/93/94/95) —
# a venue with no PCI id and no such code in its address is either a
# genuinely unreachable venue or contaminated data (confirmed: 100% the
# latter for this dataset, see the module docstring).
_RESET_SQL = """
    UPDATE venue
    SET address = NULL,
        location = NULL,
        geocode_source = NULL,
        needs_review = false
    WHERE address IS NOT NULL
      AND paris_cine_info_tid IS NULL
      AND address !~
        '(75[0-9]{3}|77[0-9]{3}|78[0-9]{3}|91[0-9]{3}|92[0-9]{3}|93[0-9]{3}|94[0-9]{3}|95[0-9]{3})'
"""


async def main() -> None:
    """Clear every contaminated address/location field."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        result = await session.exec(text(_RESET_SQL))
        await commit(session)
        print(f"Reset {result.rowcount} contaminated venue(s)")


if __name__ == "__main__":
    asyncio.run(main())
