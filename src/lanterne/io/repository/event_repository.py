"""Read/write access to screening events: ingestion, dedup, and querying.

``EventRepository.ingest`` is the single entry point for persisting scraped
sightings: it resolves the :class:`Film` and :class:`Venue` rows (delegated
to :class:`~lanterne.io.repository.film_repository.FilmRepository` and
:class:`~lanterne.io.repository.venue_repository.VenueRepository`), computes
the deduplication key, and inserts or merges the :class:`ScreeningEvent` (see
``docs/decisions/0002-dedup-merge-strategy.md`` and
``docs/decisions/0006-relational-schema-split.md``).

``EventRepository`` also exposes every film- and venue-management method it
used to implement directly, as thin delegates onto the composed
``FilmRepository``/``VenueRepository`` — this keeps every existing caller
(``pipeline.py``, ``main.py``, ``io/bot.py``,
``scripts/fix_mk2_venue_fragmentation.py``) working against a single
``EventRepository(session)`` facade, unaware of the internal split (see
``docs/architecture.md``).
"""

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_
from sqlalchemy.orm import selectinload
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from lanterne.core.dedup import compute_dedup_key
from lanterne.core.models import (
    EventSighting,
    Film,
    FilmRating,
    ScreeningEvent,
    Sighting,
    Venue,
)
from lanterne.core.qa import QueryCriteria
from lanterne.core.stats import EventStats
from lanterne.io.repository._commit import commit
from lanterne.io.repository.film_repository import FilmRepository
from lanterne.io.repository.venue_repository import VenueRepository
from lanterne.io.scrapers.base import RatingRecord, VenueDetail

_SEARCH_LIMIT = 20

# Eager-load options applied to every event query: callers format events with
# their film and venue, and lazy loading is not available on async sessions.
# The type: ignore pair is needed because SQLModel types Relationship class
# attributes as the target model, not as the ORM QueryableAttribute.
_EVENT_LOADS = (
    selectinload(ScreeningEvent.film),  # type: ignore[arg-type]
    selectinload(ScreeningEvent.venue),  # type: ignore[arg-type]
)


class EventRepository:
    """Read/write access to persisted screening events.

    Composes a :class:`FilmRepository` and :class:`VenueRepository` bound to
    the same session for its own film/venue resolution needs, and re-exposes
    their public methods as delegates so this stays the single repository
    callers need to construct.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to an active session.

        Args:
            session: Active async session bound to the target database.
        """
        self._session = session
        self._films = FilmRepository(session)
        self._venues = VenueRepository(session)

    async def ingest(self, sighting: Sighting) -> ScreeningEvent:
        """Persist a sighting, deduplicating against the stored events.

        Resolves (or creates) the film and venue rows, computes the
        deduplication key, and either inserts a new event or merges into the
        existing one (see ``docs/decisions/0002-dedup-merge-strategy.md``): the
        first source to report a screening owns its identity; later sources
        only enrich it — ``has_team_present`` and ``is_special`` become the
        logical OR of both reports, and a missing ``description``,
        ``cycle_name`` or ``booking_url`` is backfilled from the newcomer.
        ``booking_url`` itself is the sighting's direct ticket link when the
        source provides one, falling back to its ``source_url`` (the
        announcement page) otherwise — so a "Réserver" link almost always has
        somewhere useful to point, even for a source with no real booking
        link of its own (see ``core/digest.py``/``core/qa.py``). Every
        source's report is recorded as an :class:`EventSighting` (one per
        source, idempotent on re-scrape).

        A new event's ``is_special`` starts ``True`` when the sighting already
        carries a specific :class:`~lanterne.core.models.EventType` —
        every source predating the all-screenings expansion only ever
        produces a sighting once it has committed to one of the eight
        categories, so this is a correct signal by construction, not a
        placeholder (see
        ``docs/decisions/0008-drop-allocine-width-source.md``). A width
        source reporting an ordinary screening leaves ``event_type`` unset and
        starts ``is_special=False``, later upgraded by the specialness
        classification pipeline (``core/specialness/``) when it runs.

        Args:
            sighting: The announcement to persist, with its provenance.

        Returns:
            The persisted event — newly inserted or the enriched existing row —
            with its film and venue loaded.
        """
        extracted = sighting.extracted
        dedup_key = compute_dedup_key(
            extracted.title, extracted.venue, extracted.starts_at
        )
        existing = await self.get_by_dedup_key(dedup_key)
        if existing is not None:
            return await self._merge(existing, sighting)

        is_special = extracted.event_type is not None
        event = ScreeningEvent(
            dedup_key=dedup_key,
            film=await self._films.resolve(extracted.title),
            venue=await self._venues.resolve(
                extracted.venue, sighting.venue_external_id
            ),
            event_type=extracted.event_type,
            is_special=is_special,
            specialness_reasons=["curated_source"] if is_special else None,
            starts_at=extracted.starts_at,
            has_team_present=extracted.has_team_present,
            description=extracted.description,
            cycle_name=extracted.cycle_name,
            booking_url=sighting.booking_url or sighting.source_url,
        )
        self._session.add(event)
        # No refresh: expire_on_commit=False keeps the id, columns, and the
        # film/venue relationships loaded; a refresh would expire the
        # relationships and force a lazy load async sessions cannot perform.
        await commit(self._session)
        await self._record_sighting(event, sighting)
        return event

    async def list_sightings(self, event_id: int) -> list[EventSighting]:
        """Return every source's sighting of an event.

        Args:
            event_id: Primary key of the screening.

        Returns:
            The event's sightings, one per reporting source.
        """
        statement = select(EventSighting).where(EventSighting.event_id == event_id)
        result = await self._session.exec(statement)
        return list(result.all())

    async def list_film_ratings(self, film_id: int) -> list[FilmRating]:
        """Return every external site's rating of a film.

        Args:
            film_id: Primary key of the film.

        Returns:
            The film's ratings, one per source that carried one.
        """
        return await self._films.list_film_ratings(film_id)

    async def list_films_missing_imdb_id(self) -> list[Film]:
        """Return every TMDB-enriched film still missing an IMDb id.

        Returns:
            Every film with a TMDB match but no IMDb id yet.
        """
        return await self._films.list_films_missing_imdb_id()

    async def save_film(self, film: Film) -> None:
        """Persist in-place changes to a film (e.g. after TMDB enrichment).

        Args:
            film: The film row to save.
        """
        await self._films.save_film(film)

    async def find_film_by_tmdb_id(self, tmdb_id: int) -> Film | None:
        """Return the film row already matched to a TMDB id, if any.

        Args:
            tmdb_id: The TMDB identifier to look up.

        Returns:
            The film row already holding this ``tmdb_id``, or ``None``.
        """
        return await self._films.find_film_by_tmdb_id(tmdb_id)

    async def merge_film(self, loser: Film, winner: Film) -> None:
        """Repoint every screening from a duplicate film row onto the canonical one.

        Args:
            loser: The duplicate film row to retire; deleted by this call.
            winner: The film row every screening should point to instead.
        """
        await self._films.merge_film(loser, winner)

    async def find_venue_by_external_id(self, external_id: str) -> Venue | None:
        """Return the venue row already matched to a source's external id, if any.

        Args:
            external_id: The source-provided stable venue identifier to
                look up (e.g. a Paris Ciné Info theatre id).

        Returns:
            The venue row already holding this external id, or ``None``.
        """
        return await self._venues.find_venue_by_external_id(external_id)

    async def merge_venue(self, loser: Venue, winner: Venue) -> None:
        """Repoint every screening from a duplicate venue row onto the canonical one.

        Args:
            loser: The duplicate venue row to retire; deleted by this call.
            winner: The venue row every screening should point to instead.
        """
        await self._venues.merge_venue(loser, winner)

    async def save_event(self, event: ScreeningEvent) -> None:
        """Persist in-place changes to an event (e.g. after specialness classification).

        Args:
            event: The screening event row to save.
        """
        self._session.add(event)
        await commit(self._session)

    async def get_by_dedup_key(self, dedup_key: str) -> ScreeningEvent | None:
        """Return the event matching a deduplication key, if any.

        Args:
            dedup_key: The unique deduplication key to look up.

        Returns:
            The matching event with film and venue loaded, or ``None``.
        """
        statement = (
            select(ScreeningEvent)
            .where(ScreeningEvent.dedup_key == dedup_key)
            .options(*_EVENT_LOADS)
        )
        result = await self._session.exec(statement)
        return result.first()

    async def list_ordinary_screenings(self) -> list[ScreeningEvent]:
        """Return every screening not yet flagged special, film and venue loaded.

        No date bound: ``prune_ordinary_screenings`` and the scrapers' own
        near-term discovery horizon already keep the stored ordinary set to
        the currently relevant window, so a caller re-evaluating every
        not-yet-special screening (see the specialness classifier's
        end-of-run pass in ``pipeline.py``) does not need to specify one.

        Returns:
            Every ``is_special=False`` event, film and venue loaded.
        """
        statement = (
            select(ScreeningEvent)
            .where(col(ScreeningEvent.is_special).is_(False))
            .options(*_EVENT_LOADS)
        )
        result = await self._session.exec(statement)
        return list(result.all())

    async def venue_counts_by_film(self) -> dict[int, int]:
        """Count every film's distinct venues, across all films at once.

        One aggregate query for every film's ``distinct_venue_count`` (see
        ``core/specialness.py::FilmContext``), instead of one query per film
        — used by the specialness classifier's end-of-run batch pass, which
        needs this count for every currently-ordinary screening at once (see
        ``docs/performance-audit.md``'s Finding 4).

        Returns:
            A mapping of ``film_id`` to its number of distinct venues, across
            every currently stored screening (special or ordinary). A film
            with no stored screening is simply absent from the mapping.
        """
        statement = select(
            ScreeningEvent.film_id, func.count(func.distinct(ScreeningEvent.venue_id))
        ).group_by(col(ScreeningEvent.film_id))
        result = await self._session.exec(statement)
        return dict(result.all())

    async def screening_counts_by_film_and_venue(self) -> dict[tuple[int, int], int]:
        """Count every (film, venue) pair's screenings, across all pairs at once.

        One aggregate query for every ``weekly_showing_count`` (see
        ``core/specialness.py::FilmContext``), instead of one query per
        (film, venue) pair — the batched counterpart to
        :meth:`venue_counts_by_film`.

        Returns:
            A mapping of ``(film_id, venue_id)`` to its number of currently
            stored screenings (special or ordinary). A pair with no stored
            screening is simply absent from the mapping.
        """
        statement = select(
            ScreeningEvent.film_id, ScreeningEvent.venue_id, func.count()
        ).group_by(col(ScreeningEvent.film_id), col(ScreeningEvent.venue_id))
        result = await self._session.exec(statement)
        return {(film_id, venue_id): count for film_id, venue_id, count in result.all()}

    async def list_between(
        self, start: datetime, end: datetime, *, only_special: bool = False
    ) -> list[ScreeningEvent]:
        """List events starting within ``[start, end)``, soonest first.

        Args:
            start: Inclusive lower bound on ``starts_at``.
            end: Exclusive upper bound on ``starts_at``.
            only_special: When ``True``, restrict to screenings flagged
                ``is_special`` — used by the weekly digest, which stays a
                curated artifact even though the database now holds every
                screening. The Q&A bot's own search (:meth:`search`) is
                intentionally not filtered this way.

        Returns:
            Matching events (film and venue loaded) ordered by start time.
        """
        statement = (
            select(ScreeningEvent)
            .where(col(ScreeningEvent.starts_at) >= start)
            .where(col(ScreeningEvent.starts_at) < end)
        )
        if only_special:
            statement = statement.where(col(ScreeningEvent.is_special).is_(True))
        statement = statement.order_by(col(ScreeningEvent.starts_at)).options(
            *_EVENT_LOADS
        )
        result = await self._session.exec(statement)
        return list(result.all())

    async def search(self, criteria: QueryCriteria) -> list[ScreeningEvent]:
        """Return events matching a structured query, soonest first.

        Each set criterion narrows the result; an unset criterion imposes no
        constraint. Text matches the film's title or synopsis
        case-insensitively.

        Args:
            criteria: The structured filter to apply.

        Returns:
            Matching events (film and venue loaded) ordered by ascending start
            time, capped in size.
        """
        statement = select(ScreeningEvent)
        if criteria.event_type is not None:
            statement = statement.where(
                ScreeningEvent.event_type == criteria.event_type
            )
        if criteria.team_only:
            statement = statement.where(col(ScreeningEvent.has_team_present).is_(True))
        if criteria.starts_after is not None:
            statement = statement.where(
                col(ScreeningEvent.starts_at) >= criteria.starts_after
            )
        if criteria.starts_before is not None:
            statement = statement.where(
                col(ScreeningEvent.starts_at) < criteria.starts_before
            )
        if criteria.text_query:
            pattern = f"%{criteria.text_query}%"
            statement = statement.join(Film).where(
                or_(
                    col(Film.title).ilike(pattern),
                    col(Film.overview).ilike(pattern),
                )
            )
        statement = (
            statement.order_by(col(ScreeningEvent.starts_at))
            .limit(_SEARCH_LIMIT)
            .options(*_EVENT_LOADS)
        )
        result = await self._session.exec(statement)
        return list(result.all())

    async def stats(self) -> EventStats:
        """Compute a summary snapshot of the stored events.

        Returns:
            An :class:`EventStats` with totals, breakdowns, and the date range.
        """
        result = await self._session.exec(select(ScreeningEvent).options(*_EVENT_LOADS))
        events = list(result.all())
        if not events:
            return EventStats(total=0)
        sightings = await self._session.exec(select(EventSighting))
        starts = sorted(event.starts_at for event in events)
        by_type = Counter(
            event.event_type.value if event.event_type is not None else "regular"
            for event in events
        )
        by_venue_kind = Counter(event.venue.kind.value for event in events)
        return EventStats(
            total=len(events),
            by_source=dict(Counter(s.source.value for s in sightings.all())),
            by_type=dict(by_type),
            by_venue_kind=dict(by_venue_kind),
            special_count=sum(1 for event in events if event.is_special),
            team_present=sum(1 for event in events if event.has_team_present),
            enriched=sum(1 for event in events if event.film.tmdb_id is not None),
            first_starts_at=starts[0],
            last_starts_at=starts[-1],
        )

    async def prune_ordinary_screenings(self, older_than: datetime) -> int:
        """Delete past ordinary screenings, bounding the database's growth.

        Only screenings with ``is_special is False`` are eligible — a special
        screening is kept regardless of age (low volume, historical/audit
        value). ``Film`` and ``Venue`` rows are left untouched: they are
        cheap and reused if the same film or venue reappears.

        SQLite does not cascade-delete on this schema's foreign keys (no
        ``PRAGMA foreign_keys=ON`` is set, and ``EventSighting.event_id``
        declares no ``ondelete``), so each target event's sightings are
        deleted explicitly before the event itself, in the same transaction.

        Args:
            older_than: Exclusive upper bound on ``starts_at`` — a screening
                starting at or after this moment is kept.

        Returns:
            Number of screening events deleted.
        """
        statement = select(ScreeningEvent.id).where(
            col(ScreeningEvent.is_special).is_(False),
            col(ScreeningEvent.starts_at) < older_than,
        )
        result = await self._session.exec(statement)
        event_ids = list(result.all())
        if not event_ids:
            return 0
        await self._session.exec(
            delete(EventSighting).where(col(EventSighting.event_id).in_(event_ids))
        )
        await self._session.exec(
            delete(ScreeningEvent).where(col(ScreeningEvent.id).in_(event_ids))
        )
        await commit(self._session)
        return len(event_ids)

    async def update_venue_passes(self, passes: dict[str, list[str]]) -> None:
        """Enrich already-stored venues with their accepted subscription cards.

        Args:
            passes: A mapping of venue display name to its accepted card
                codes.
        """
        await self._venues.update_venue_passes(passes)

    async def update_venue_details(self, details: dict[str, VenueDetail]) -> None:
        """Enrich already-stored venues with address/website/room detail.

        Args:
            details: A mapping of venue display name to its
                :class:`~lanterne.io.scrapers.base.VenueDetail`.
        """
        await self._venues.update_venue_details(details)

    async def update_film_ratings(self, ratings: dict[str, list[RatingRecord]]) -> None:
        """Upsert already-enriched films' ratings, matched by IMDb id.

        Args:
            ratings: A mapping of IMDb id to that film's freshly-fetched
                :class:`~lanterne.io.scrapers.base.RatingRecord` list.
        """
        await self._films.update_film_ratings(ratings)

    async def _merge(
        self, existing: ScreeningEvent, sighting: Sighting
    ) -> ScreeningEvent:
        """Merge a colliding sighting into the stored event and return it."""
        extracted = sighting.extracted
        existing.has_team_present = (
            existing.has_team_present or extracted.has_team_present
        )
        if extracted.event_type is not None and not existing.is_special:
            existing.is_special = True
            existing.specialness_reasons = ["curated_source"]
        if existing.description is None:
            existing.description = extracted.description
        if existing.cycle_name is None:
            existing.cycle_name = extracted.cycle_name
        if existing.booking_url is None:
            existing.booking_url = sighting.booking_url or sighting.source_url
        self._session.add(existing)
        await commit(self._session)
        await self._record_sighting(existing, sighting)
        return existing

    async def _record_sighting(self, event: ScreeningEvent, sighting: Sighting) -> None:
        """Record the sighting for its source, once (idempotent on re-scrape)."""
        statement = select(EventSighting).where(
            EventSighting.event_id == event.id,
            EventSighting.source == sighting.source,
        )
        result = await self._session.exec(statement)
        if result.first() is not None:
            return
        self._session.add(
            EventSighting(
                event_id=event.id,
                source=sighting.source,
                source_url=sighting.source_url,
                scraped_at=datetime.now(UTC),
            )
        )
        await commit(self._session)
