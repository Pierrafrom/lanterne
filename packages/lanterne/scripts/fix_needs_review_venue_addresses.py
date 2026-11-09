"""One-off cleanup: replace 7 of 8 ``needs_review`` addresses with offi.fr's own.

Investigated live (2026-07-21): every ``needs_review`` venue (a low-confidence
``NAME_SEARCH`` geocode — no street address was ever stored, only the venue's
name, so ``geocode-venues`` had to guess a point from the name alone) is
sourced from ``OFFI``, whose own per-venue page carries a real, addressed
``schema.org/PostalAddress`` block (``itemprop="streetAddress"`` /
``"postalCode"`` / ``"addressLocality"``) that the ``offi.py`` scraper
doesn't currently parse (it only reads the programme grid — see its module
docstring). Matching each venue's stored name against offi.fr's directory
found the correct page for 7 of the 8; several previous guesses were not
just imprecise but in the wrong city entirely (e.g. "L'Étoile" guessed as
Paris's Place Charles de Gaulle is actually in La Courneuve; "Rex" guessed
inside Disneyland Paris is actually in Châtenay-Malabry).

``La Grange`` (id 139) is deliberately left alone: offi.fr's own directory
has *two* different cinemas named "La Grange" (Ferme de la Tremblaye, Bois-
d'Arcy, 78 vs Ferme d'Ayau, Roissy-en-Brie, 77), and this row's stored
address (Wissous, 91) matches neither — comparing both candidates' current
programme against this row's stored screenings did not disambiguate (both
show largely the same wide releases this week). Left ``needs_review`` for a
human to resolve with more specific information than a title/showtime diff
can provide.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fix_needs_review_venue_addresses.py
    uv run lanterne geocode-venues
"""

import asyncio

from lanterne.config import Settings
from lanterne.core.models import Venue
from lanterne.io.db import Database
from lanterne.io.repository._commit import commit

# venue id -> corrected address, each confirmed live against the venue's own
# offi.fr page's schema.org address block:
#   224 Pandora   -> offi.fr/cinema/pandora-2916.html
#   160 Eden      -> offi.fr/cinema/eden-5831.html
#   237 Rex       -> offi.fr/cinema/rex-3070.html
#   117 Calypso   -> offi.fr/cinema/calypso-1611.html
#   276 Lanterne  -> offi.fr/theatre/la-lanterne-6972.html
#   208 Lucarne   -> offi.fr/cinema/lucarne-2618.html
#   205 L'Étoile  -> offi.fr/cinema/letoile-2597.html
_ADDRESS_FIXES: dict[int, str] = {
    224: "6 allée Simone-Signoret, place du Marché, 78260 Achères",
    160: "5 bis rue de Pontoise, 95160 Montmorency",
    237: "364 avenue de la Division-Leclerc, 92290 Châtenay-Malabry",
    117: "38 rue Victor-Basch, 91170 Viry-Châtillon",
    276: "2 rue de la Légion d'Honneur, 78120 Rambouillet",
    208: "100 rue Juliette-Savar, 94000 Créteil",
    205: "1 allée du Progrès, 93120 La Courneuve",
}


async def main() -> None:
    """Correct each venue's address and clear its stale low-confidence geocode."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        for venue_id, address in _ADDRESS_FIXES.items():
            venue = await session.get(Venue, venue_id)
            if venue is None:
                print(f"  venue {venue_id}: not found, skipping")
                continue
            print(f"  '{venue.name}': address corrected to '{address}'")
            venue.address = address
            venue.location = None
            venue.geocode_source = None
            venue.needs_review = False
            session.add(venue)
        await commit(session)
        print("Done — 'La Grange' (id 139) left needs_review, see module docstring")


if __name__ == "__main__":
    asyncio.run(main())
