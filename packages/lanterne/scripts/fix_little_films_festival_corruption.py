"""One-off cleanup: repoint 11 screenings corrupted by a venue/title merge bug.

Confirmed live (2026-07-17): 11 screenings from the "Little Films Festival"
(a themed children's summer program run across several real Multiciné/
Chaplin venues) were corrupted by the exact historical bug already
documented and discarded going forward in
``io/scrapers/paris_cine_info.py`` (see
``test_fetch_events_discards_the_llms_title_venue_and_time_guesses``): an
early version trusted the LLM's own title/venue output for a commented
showtime, which occasionally merged the real venue name into the film
title (or vice versa) and fell back to a placeholder venue name ("null",
"No venue specified", "Not specified in the text", or the announced
film/festival name itself). These 11 rows predate that fix and were never
cleaned up.

Every affected screening's real venue and film were recovered from its
booking URL (which subdomain: ``parnassiens``, ``denfert``, or
``cinqcaumartin``) and independent web research confirming the real film
titles ("Animo rigolo", "Rita et Crocodile", "Les Tourouges et les
Toubleus" are all real, existing films/programs — not garbled text). Every
target Venue and Film row already exists cleanly in the database from
other, correctly-parsed screenings of the same festival — this script only
repoints, it creates nothing.

See docs/decisions/0024-venue-data-quality-cleanup.md for the full
investigation.

Usage::

    uv run lanterne backup-db   # always back up first
    uv run python packages/lanterne/scripts/fix_little_films_festival_corruption.py
"""

import asyncio

from sqlmodel import col, delete, select

from lanterne.config import Settings
from lanterne.core.models import Film, ScreeningEvent, Venue
from lanterne.io.db import Database
from lanterne.io.repository._commit import commit

# screening_id -> (correct venue_id, correct film_id), recovered from each
# screening's booking URL and cross-checked against independent web
# research on the real film titles.
_CORRECTIONS: dict[int, tuple[int, int]] = {
    257: (41, 144),  # Les 7 Parnassiens / Animo rigolo
    261: (95, 144),  # Chaplin Denfert / Animo rigolo
    259: (95, 144),  # Chaplin Denfert / Animo rigolo
    294: (41, 298),  # Les 7 Parnassiens / Rita et Crocodile
    266: (41, 150),  # Les 7 Parnassiens / Les Tourouges et les Toubleus
    267: (41, 150),  # Les 7 Parnassiens / Les Tourouges et les Toubleus
    271: (74, 150),  # Les 5 Caumartin / Les Tourouges et les Toubleus
    291: (41, 298),  # Les 7 Parnassiens / Rita et Crocodile
    292: (41, 298),  # Les 7 Parnassiens / Rita et Crocodile
    270: (41, 150),  # Les 7 Parnassiens / Les Tourouges et les Toubleus
    293: (41, 298),  # Les 7 Parnassiens / Rita et Crocodile
}

# Fake venue rows this cleanup empties out entirely — safe to delete once
# every screening above has been repointed.
_FAKE_VENUE_IDS = (87, 88, 90, 91, 92, 93)

# Garbled film rows (venue name merged into the title, or vice versa) this
# cleanup empties out entirely — safe to delete once repointed.
_GARBLED_FILM_IDS = (143, 145, 152, 153)


async def main() -> None:
    """Repoint every corrupted screening, then delete the emptied fake rows."""
    settings = Settings()
    db = Database(settings.database_url)
    async with db.session() as session:
        for screening_id, (venue_id, film_id) in _CORRECTIONS.items():
            screening = await session.get(ScreeningEvent, screening_id)
            if screening is None:
                print(f"  screening {screening_id}: not found, skipping")
                continue
            screening.venue_id = venue_id
            screening.film_id = film_id
            session.add(screening)
            print(f"  screening {screening_id} -> venue={venue_id} film={film_id}")
        await commit(session)

        remaining_venue_refs = await session.exec(
            select(ScreeningEvent.id).where(
                col(ScreeningEvent.venue_id).in_(_FAKE_VENUE_IDS)
            )
        )
        remaining_film_refs = await session.exec(
            select(ScreeningEvent.id).where(
                col(ScreeningEvent.film_id).in_(_GARBLED_FILM_IDS)
            )
        )
        still_referenced = (
            remaining_venue_refs.first() is not None
            or remaining_film_refs.first() is not None
        )
        if still_referenced:
            print("Some fake venue/film rows still referenced — aborting cleanup")
            return

        await session.exec(delete(Venue).where(col(Venue.id).in_(_FAKE_VENUE_IDS)))
        await session.exec(delete(Film).where(col(Film.id).in_(_GARBLED_FILM_IDS)))
        await commit(session)
        print(
            f"Deleted {len(_FAKE_VENUE_IDS)} fake venues, "
            f"{len(_GARBLED_FILM_IDS)} garbled films"
        )


if __name__ == "__main__":
    asyncio.run(main())
