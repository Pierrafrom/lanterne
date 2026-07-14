"""Decision-support tool for the MK2/Le Champo/Le Louxor retirement spot-check.

`docs/coverage-matrix.md` recommends retiring these three bespoke scrapers
once Paris Ciné Info is confirmed to already cover the same ground — see
``docs/decisions/0008-drop-allocine-width-source.md`` and the "Still open"
section of the coverage matrix. This script answers the concrete question a
retirement decision needs: for every screening the bespoke source reported,
does Paris Ciné Info also carry it?

Because ``EventRepository.ingest`` deduplicates on (film, venue, start time)
and merges same-screening sightings from different sources into one
``ScreeningEvent`` row (see ADR 0002), there is nothing to field-by-field
diff once two sources agree on a screening — team presence, cycle name, and
event type are already unified into that one row. The only question left is
presence: a screening with no ``Source.PARIS_CINE_INFO`` sighting is a real
regression risk if the bespoke scraper is retired; one with such a sighting
is already safely covered.

Run against a database that already has a real ``scrape`` in it — this
produces no meaningful output against an empty or synthetic database:

    uv run python scripts/compare_source_coverage.py mk2.com
    uv run python scripts/compare_source_coverage.py cinema-lechampo.com
    uv run python scripts/compare_source_coverage.py cinemalouxor.fr
"""

import argparse
import asyncio
from dataclasses import dataclass

from sqlalchemy.orm import selectinload
from sqlmodel import select

from cine_event_bot.config import Settings
from cine_event_bot.core.models import EventSighting, ScreeningEvent, Source
from cine_event_bot.io.db import Database

_RETIREMENT_CANDIDATES = (Source.MK2, Source.LE_CHAMPO, Source.LE_LOUXOR)

# Async sessions cannot lazy-load relationships — same constraint as
# io/repository.py's _EVENT_LOADS, duplicated here rather than imported since
# that constant is private to the repository module.
_EVENT_LOADS = (selectinload(ScreeningEvent.film), selectinload(ScreeningEvent.venue))


@dataclass(frozen=True, slots=True)
class _Coverage:
    """One bespoke-source screening's coverage status on Paris Ciné Info."""

    event: ScreeningEvent
    also_on_paris_cine_info: bool


async def _load_coverage(database: Database, source: Source) -> list[_Coverage]:
    """Load every screening the source reported, flagged by Paris Ciné Info overlap."""
    async with database.session() as session:
        statement = (
            select(ScreeningEvent)
            .join(EventSighting, EventSighting.event_id == ScreeningEvent.id)
            .where(EventSighting.source == source)
            .options(*_EVENT_LOADS)
        )
        result = await session.exec(statement)
        events = list(result.all())

        coverage = []
        for event in events:
            paris_cine_info_statement = select(EventSighting).where(
                EventSighting.event_id == event.id,
                EventSighting.source == Source.PARIS_CINE_INFO,
            )
            paris_cine_info_result = await session.exec(paris_cine_info_statement)
            coverage.append(
                _Coverage(
                    event=event,
                    also_on_paris_cine_info=paris_cine_info_result.first() is not None,
                )
            )
        return coverage


def _print_report(source: Source, coverage: list[_Coverage]) -> None:
    """Print the regression-risk screenings and a summary count."""
    at_risk = [c for c in coverage if not c.also_on_paris_cine_info]
    covered = len(coverage) - len(at_risk)

    print(f"\n=== {source.value} — {len(coverage)} screening(s) total ===")
    print(f"Also on paris-cine.info: {covered}")
    print(f"NOT on paris-cine.info (regression risk if retired): {len(at_risk)}")
    for c in at_risk:
        event = c.event
        print(
            f"  - {event.starts_at.isoformat()}  {event.film.title!r} @ "
            f"{event.venue.name!r}  event_type={event.event_type}  "
            f"team={event.has_team_present}  cycle={event.cycle_name!r}"
        )


async def _run(source: Source | None) -> None:
    database = Database(Settings().database_url)
    await database.migrate_to_head()
    try:
        sources = [source] if source is not None else list(_RETIREMENT_CANDIDATES)
        for one_source in sources:
            coverage = await _load_coverage(database, one_source)
            _print_report(one_source, coverage)
    finally:
        await database.dispose()


def _parse_source(value: str) -> Source:
    try:
        return Source(value)
    except ValueError as error:
        choices = ", ".join(member.value for member in _RETIREMENT_CANDIDATES)
        raise argparse.ArgumentTypeError(
            f"unknown source {value!r} — expected one of: {choices}"
        ) from error


def main() -> None:
    """Parse CLI arguments and print the coverage report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=_parse_source,
        nargs="?",
        default=None,
        help="Source domain to spot-check (default: all three candidates).",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.source))


if __name__ == "__main__":
    main()
