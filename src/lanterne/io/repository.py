"""Repositories mediating access to screening events and subscribers.

Each repository wraps an injected :class:`AsyncSession` and owns the commit for
its write operations, so callers work in terms of domain intent
(``ingest`` a sighting, ``subscribe`` a chat) rather than session mechanics.

``EventRepository.ingest`` is the single entry point for persisting scraped
sightings: it resolves the :class:`Film` and :class:`Venue` rows, computes the
deduplication key, and inserts or merges the :class:`ScreeningEvent` (see
``docs/decisions/0002-dedup-merge-strategy.md`` and
``docs/decisions/0006-relational-schema-split.md``).
"""

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, update
from sqlalchemy.orm import selectinload
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from lanterne.core.dedup import (
    canonicalize_venue_name,
    compute_dedup_key,
    normalize_text,
)
from lanterne.core.models import (
    EventSighting,
    Film,
    FilmRating,
    ScreeningEvent,
    Sighting,
    Subscriber,
    Venue,
)
from lanterne.core.qa import QueryCriteria
from lanterne.core.stats import EventStats
from lanterne.core.venues import classify_venue_kind
from lanterne.io.scrapers.base import RatingRecord, VenueDetail

_SEARCH_LIMIT = 20


async def _commit(session: AsyncSession) -> None:
    """Commit the session, rolling back on failure to keep it reusable.

    An uncaught commit failure (e.g. a unique-constraint collision) leaves an
    AsyncSession in SQLAlchemy's "pending rollback" state, where every
    subsequent operation raises ``PendingRollbackError`` regardless of what it
    does — turning one bad row into a crash of the whole ingestion run instead
    of the one enrichment or sighting that actually failed.
    """
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise


def _apply_venue_detail(venue: Venue, detail: VenueDetail) -> bool:
    """Copy every known field of ``detail`` onto ``venue``, in place.

    A ``None`` field on ``detail`` never overwrites an already-known value —
    the endpoint omitting a field (or the venue having multiple rooms this
    run, see :class:`~lanterne.io.scrapers.base.VenueDetailSource`)
    is not evidence the value changed to unknown.

    Args:
        venue: The venue row to update, mutated in place.
        detail: The freshly-fetched detail to apply.

    Returns:
        Whether any field actually changed.
    """
    changed = False
    fields = ("address", "website", "seat_count", "screen_width_m", "screen_height_m")
    for field in fields:
        value = getattr(detail, field)
        if value is not None and getattr(venue, field) != value:
            setattr(venue, field, value)
            changed = True
    return changed


# Eager-load options applied to every event query: callers format events with
# their film and venue, and lazy loading is not available on async sessions.
# The type: ignore pair is needed because SQLModel types Relationship class
# attributes as the target model, not as the ORM QueryableAttribute.
_EVENT_LOADS = (
    selectinload(ScreeningEvent.film),  # type: ignore[arg-type]
    selectinload(ScreeningEvent.venue),  # type: ignore[arg-type]
)


class EventRepository:
    """Read/write access to persisted screening events."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to an active session.

        Args:
            session: Active async session bound to the target database.
        """
        self._session = session

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
            film=await self._resolve_film(extracted.title),
            venue=await self._resolve_venue(
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
        await _commit(self._session)
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
            The film's ratings, one per source that carried one (see
            ``docs/decisions/0013-film-ratings-from-paris-cine-info.md``).
        """
        statement = select(FilmRating).where(FilmRating.film_id == film_id)
        result = await self._session.exec(statement)
        return list(result.all())

    async def list_films_missing_imdb_id(self) -> list[Film]:
        """Return every TMDB-enriched film still missing an IMDb id.

        Used by the one-off ``backfill-ratings`` CLI command to catch up
        films enriched before ``Film.imdb_id``/``backdrop_url`` existed
        (see ``docs/decisions/0013-film-ratings-from-paris-cine-info.md``)
        — the normal enrichment path only ever touches a film once
        (``IngestionPipeline._enrich``'s ``tmdb_id is not None`` guard), so
        these are otherwise never revisited.

        Returns:
            Every film with a TMDB match but no IMDb id yet.
        """
        statement = select(Film).where(
            col(Film.tmdb_id).is_not(None), col(Film.imdb_id).is_(None)
        )
        result = await self._session.exec(statement)
        return list(result.all())

    async def save_film(self, film: Film) -> None:
        """Persist in-place changes to a film (e.g. after TMDB enrichment).

        Args:
            film: The film row to save.
        """
        self._session.add(film)
        await _commit(self._session)

    async def find_film_by_tmdb_id(self, tmdb_id: int) -> Film | None:
        """Return the film row already matched to a TMDB id, if any.

        Used by ``pipeline.py::IngestionPipeline._enrich`` to detect a
        duplicate film row before saving a fresh TMDB match — two
        differently-worded announced titles for the same real film (a
        punctuation variant ``core/dedup.py::normalize_text`` does not
        unify) resolve to the same ``tmdb_id`` but started as two different
        ``Film`` rows. Called right after a caller sets an in-memory
        ``film.tmdb_id`` it has not saved yet, so autoflush is suspended for
        this query — otherwise SQLAlchemy would flush that pending change
        first and raise the very collision this method exists to detect
        before it, instead of returning the existing row cleanly.

        Args:
            tmdb_id: The TMDB identifier to look up.

        Returns:
            The film row already holding this ``tmdb_id``, or ``None``.
        """
        statement = select(Film).where(Film.tmdb_id == tmdb_id)
        with self._session.no_autoflush:
            result = await self._session.exec(statement)
            return result.first()

    async def merge_film(self, loser: Film, winner: Film) -> None:
        """Repoint every screening from a duplicate film row onto the canonical one.

        Called when two announced titles turn out to be the same TMDB film:
        ``winner`` already legitimately owns the ``tmdb_id``, so ``loser``
        (still unenriched — its own TMDB match was never saved) is retired
        rather than left as a dead-end duplicate with no metadata. Bulk SQL
        statements, not per-row ORM loads — ``loser`` may already have many
        screenings attached.

        Args:
            loser: The duplicate film row to retire; deleted by this call.
            winner: The film row every screening should point to instead.
        """
        loser_id, winner_id = loser.id, winner.id
        # loser still carries an unsaved, colliding tmdb_id in memory (the
        # enrichment that revealed the duplicate never got to save it) — drop
        # it from the session's unit of work so committing the bulk
        # statements below never tries to flush that pending write too.
        self._session.expunge(loser)
        await self._session.exec(
            update(ScreeningEvent)
            .where(col(ScreeningEvent.film_id) == loser_id)
            .values(film_id=winner_id)
        )
        await self._session.exec(delete(Film).where(col(Film.id) == loser_id))
        await _commit(self._session)

    async def find_venue_by_external_id(self, external_id: str) -> Venue | None:
        """Return the venue row already matched to a source's external id, if any.

        Used by :meth:`_resolve_venue` to recognize the same physical venue
        across sources that describe it with different wording — see
        ``Venue.paris_cine_info_tid``.

        Args:
            external_id: The source-provided stable venue identifier to
                look up (e.g. a Paris Ciné Info theatre id).

        Returns:
            The venue row already holding this external id, or ``None``.
        """
        statement = select(Venue).where(Venue.paris_cine_info_tid == external_id)
        result = await self._session.exec(statement)
        return result.first()

    async def merge_venue(self, loser: Venue, winner: Venue) -> None:
        """Repoint every screening from a duplicate venue row onto the canonical one.

        Called when a source's stable external id reveals that two
        differently-worded venue names are the same physical venue.
        ``Venue`` has a single dependent foreign key
        (``ScreeningEvent.venue_id``), so the merge is a straightforward bulk
        repoint-then-delete, no per-row ORM loads needed — same shape as
        :meth:`merge_film`.

        Args:
            loser: The duplicate venue row to retire; deleted by this call.
            winner: The venue row every screening should point to instead.
        """
        loser_id, winner_id = loser.id, winner.id
        await self._session.exec(
            update(ScreeningEvent)
            .where(col(ScreeningEvent.venue_id) == loser_id)
            .values(venue_id=winner_id)
        )
        await self._session.exec(delete(Venue).where(col(Venue.id) == loser_id))
        await _commit(self._session)

    async def save_event(self, event: ScreeningEvent) -> None:
        """Persist in-place changes to an event (e.g. after specialness classification).

        Args:
            event: The screening event row to save.
        """
        self._session.add(event)
        await _commit(self._session)

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
        await _commit(self._session)
        return len(event_ids)

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
        await _commit(self._session)
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
        await _commit(self._session)

    async def update_venue_passes(self, passes: dict[str, list[str]]) -> None:
        """Enrich already-stored venues with their accepted subscription cards.

        Only enriches venues that already exist from an actual screening —
        never creates a ``Venue`` row from this data alone, since a venue
        with no stored screening is out of scope regardless of which passes
        it accepts (see ``pipeline.py::IngestionPipeline._apply_venue_passes``
        and ``ParisCineInfoScraper.fetch_venue_passes``).

        Args:
            passes: A mapping of venue display name (as announced by the
                source) to its accepted card codes, matched to a stored
                venue via the same slug/alias resolution as
                :meth:`_resolve_venue`.
        """
        changed = False
        for name, cards in passes.items():
            venue = await self._find_venue(name)
            if venue is None:
                continue
            sorted_cards = sorted(set(cards))
            if venue.accepted_passes != sorted_cards:
                venue.accepted_passes = sorted_cards
                self._session.add(venue)
                changed = True
        if changed:
            await _commit(self._session)

    async def update_venue_details(self, details: dict[str, VenueDetail]) -> None:
        """Enrich already-stored venues with address/website/room detail.

        Same scope rule as :meth:`update_venue_passes`: only enriches venues
        that already exist from an actual screening, matched via the same
        slug/alias resolution.

        Args:
            details: A mapping of venue display name (as announced by the
                source) to its :class:`~lanterne.io.scrapers.base.VenueDetail`
                (see ``ParisCineInfoScraper.fetch_venue_details``).
        """
        changed = False
        for name, detail in details.items():
            venue = await self._find_venue(name)
            if venue is None:
                continue
            if _apply_venue_detail(venue, detail):
                self._session.add(venue)
                changed = True
        if changed:
            await _commit(self._session)

    async def update_film_ratings(self, ratings: dict[str, list[RatingRecord]]) -> None:
        """Upsert already-enriched films' ratings, matched by IMDb id.

        Only enriches films that already carry a ``Film.imdb_id`` from TMDB
        enrichment — never creates a ``Film`` row from this data alone,
        same scope rule as :meth:`update_venue_passes` (see
        ``pipeline.py::IngestionPipeline._apply_film_ratings`` and
        ``ParisCineInfoScraper.fetch_film_ratings``).

        Args:
            ratings: A mapping of IMDb id to that film's freshly-fetched
                :class:`~lanterne.io.scrapers.base.RatingRecord`
                list.
        """
        changed = False
        for imdb_id, records in ratings.items():
            film = await self._find_film_by_imdb_id(imdb_id)
            if film is None or film.id is None:
                continue
            for record in records:
                if await self._upsert_film_rating(film.id, record):
                    changed = True
        if changed:
            await _commit(self._session)

    async def _find_film_by_imdb_id(self, imdb_id: str) -> Film | None:
        """Return the film row matched to an IMDb id, or None."""
        statement = select(Film).where(Film.imdb_id == imdb_id)
        result = await self._session.exec(statement)
        return result.first()

    async def _upsert_film_rating(self, film_id: int, record: RatingRecord) -> bool:
        """Insert or refresh one film's rating for one source.

        Returns:
            Whether the stored row was created or changed.
        """
        statement = select(FilmRating).where(
            FilmRating.film_id == film_id, FilmRating.source == record.source
        )
        result = await self._session.exec(statement)
        existing = result.first()
        now = datetime.now(UTC)
        if existing is None:
            self._session.add(
                FilmRating(
                    film_id=film_id,
                    source=record.source,
                    rating=record.rating,
                    url=record.url,
                    fetched_at=now,
                )
            )
            return True
        if existing.rating == record.rating and existing.url == record.url:
            return False
        existing.rating = record.rating
        existing.url = record.url
        existing.fetched_at = now
        self._session.add(existing)
        return True

    async def _find_venue(self, name: str) -> Venue | None:
        """Return the venue row matching an announced name, or None."""
        slug = normalize_text(canonicalize_venue_name(name))
        statement = select(Venue).where(Venue.slug == slug)
        result = await self._session.exec(statement)
        return result.first()

    async def _resolve_film(self, title: str) -> Film:
        """Return the film row for an announced title, creating it if new."""
        title_key = normalize_text(title)
        statement = select(Film).where(Film.title_key == title_key)
        result = await self._session.exec(statement)
        existing = result.first()
        if existing is not None:
            return existing
        return Film(title_key=title_key, title=title)

    async def _resolve_venue(self, name: str, external_id: str | None) -> Venue:
        """Return the venue row for an announced name, creating it if new.

        When the source provides a stable ``external_id`` (see
        ``Venue.paris_cine_info_tid``), it is checked first and takes
        priority over the name-based ``slug`` match: it is the more reliable
        signal that two differently-worded names refer to the same physical
        venue. A row already matched by ``external_id`` is always the one
        returned; a *different* row also matched by ``slug`` is retired into
        it via :meth:`merge_venue` — this is what lets a pre-existing
        fragmented venue (created before its external id was known, or from
        a source with no such id) self-heal into one row the next time a
        showtime carrying the id is ingested (see
        ``docs/decisions/0012-retire-lechampo.md``'s venue-name
        fragmentation follow-up). A slug match with no external id yet is
        stamped with one rather than merged, since no other row claims it.

        Args:
            name: Venue name as announced by the source.
            external_id: The source's stable venue identifier, or ``None``
                for a source that does not provide one.

        Returns:
            The resolved (possibly newly created, possibly just-merged-into)
            venue row.
        """
        tid_match = (
            await self.find_venue_by_external_id(external_id)
            if external_id is not None
            else None
        )
        slug_match = await self._find_venue(name)
        if tid_match is not None:
            if slug_match is not None and slug_match.id != tid_match.id:
                await self.merge_venue(loser=slug_match, winner=tid_match)
            return tid_match
        if slug_match is not None:
            if external_id is not None and slug_match.paris_cine_info_tid is None:
                slug_match.paris_cine_info_tid = external_id
            return slug_match
        canonical = canonicalize_venue_name(name)
        slug = normalize_text(canonical)
        return Venue(
            slug=slug,
            name=canonical,
            kind=classify_venue_kind(canonical),
            paris_cine_info_tid=external_id,
        )


class SubscriberRepository:
    """Read/write access to weekly-digest subscribers."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to an active session.

        Args:
            session: Active async session bound to the target database.
        """
        self._session = session

    async def subscribe(self, chat_id: int) -> Subscriber:
        """Opt a chat in to the digest, reactivating it if already known.

        Idempotent: subscribing an active chat is a no-op, and subscribing a
        previously unsubscribed chat flips it back to active without losing its
        original subscription timestamp.

        Args:
            chat_id: Telegram chat identifier.

        Returns:
            The active subscriber row for that chat.
        """
        subscriber = await self._session.get(Subscriber, chat_id)
        if subscriber is None:
            subscriber = Subscriber(
                chat_id=chat_id, subscribed_at=datetime.now(UTC), is_active=True
            )
        else:
            subscriber.is_active = True
        self._session.add(subscriber)
        await _commit(self._session)
        await self._session.refresh(subscriber)
        return subscriber

    async def unsubscribe(self, chat_id: int) -> None:
        """Opt a chat out of the digest; no-op if the chat is unknown.

        The row is kept and flagged inactive rather than deleted, preserving the
        subscription history.

        Args:
            chat_id: Telegram chat identifier.
        """
        subscriber = await self._session.get(Subscriber, chat_id)
        if subscriber is None:
            return
        subscriber.is_active = False
        self._session.add(subscriber)
        await _commit(self._session)

    async def list_active(self) -> list[Subscriber]:
        """Return every chat currently opted in to the digest.

        Returns:
            All subscribers whose ``is_active`` flag is ``True``.
        """
        statement = select(Subscriber).where(col(Subscriber.is_active).is_(True))
        result = await self._session.exec(statement)
        return list(result.all())
