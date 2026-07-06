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
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import or_
from sqlalchemy.orm import selectinload
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from cine_event_bot.core.dedup import compute_dedup_key, normalize_text
from cine_event_bot.core.models import (
    EventSighting,
    Film,
    ScreeningEvent,
    Sighting,
    Subscriber,
    Venue,
)
from cine_event_bot.core.qa import QueryCriteria

_SEARCH_LIMIT = 20

# Eager-load options applied to every event query: callers format events with
# their film and venue, and lazy loading is not available on async sessions.
# The type: ignore pair is needed because SQLModel types Relationship class
# attributes as the target model, not as the ORM QueryableAttribute.
_EVENT_LOADS = (
    selectinload(ScreeningEvent.film),  # type: ignore[arg-type]
    selectinload(ScreeningEvent.venue),  # type: ignore[arg-type]
)


@dataclass(frozen=True, slots=True)
class EventStats:
    """A snapshot of the stored events, for the ``stats`` command.

    Attributes:
        total: Number of events stored.
        by_source: Sighting count per source domain — an event reported by two
            sources contributes to both counts.
        by_type: Event count per event type.
        team_present: Number of events with the film team present.
        enriched: Number of events whose film has a TMDB match.
        first_starts_at: Earliest screening start, or None when empty.
        last_starts_at: Latest screening start, or None when empty.
    """

    total: int
    by_source: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    team_present: int = 0
    enriched: int = 0
    first_starts_at: datetime | None = None
    last_starts_at: datetime | None = None


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
        only enrich it — ``has_team_present`` becomes the logical OR of both
        reports, and a missing ``description``, ``cycle_name`` or
        ``booking_url`` is backfilled from the newcomer. Every source's report
        is recorded as an :class:`EventSighting` (one per source, idempotent
        on re-scrape).

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

        event = ScreeningEvent(
            dedup_key=dedup_key,
            film=await self._resolve_film(extracted.title),
            venue=await self._resolve_venue(extracted.venue),
            event_type=extracted.event_type,
            starts_at=extracted.starts_at,
            has_team_present=extracted.has_team_present,
            description=extracted.description,
            cycle_name=extracted.cycle_name,
            booking_url=sighting.booking_url,
        )
        self._session.add(event)
        # No refresh: expire_on_commit=False keeps the id, columns, and the
        # film/venue relationships loaded; a refresh would expire the
        # relationships and force a lazy load async sessions cannot perform.
        await self._session.commit()
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

    async def save_film(self, film: Film) -> None:
        """Persist in-place changes to a film (e.g. after TMDB enrichment).

        Args:
            film: The film row to save.
        """
        self._session.add(film)
        await self._session.commit()

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

    async def list_between(
        self, start: datetime, end: datetime
    ) -> list[ScreeningEvent]:
        """List events starting within ``[start, end)``, soonest first.

        Args:
            start: Inclusive lower bound on ``starts_at``.
            end: Exclusive upper bound on ``starts_at``.

        Returns:
            Matching events (film and venue loaded) ordered by start time.
        """
        statement = (
            select(ScreeningEvent)
            .where(col(ScreeningEvent.starts_at) >= start)
            .where(col(ScreeningEvent.starts_at) < end)
            .order_by(col(ScreeningEvent.starts_at))
            .options(*_EVENT_LOADS)
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
        return EventStats(
            total=len(events),
            by_source=dict(Counter(s.source.value for s in sightings.all())),
            by_type=dict(Counter(event.event_type.value for event in events)),
            team_present=sum(1 for event in events if event.has_team_present),
            enriched=sum(1 for event in events if event.film.tmdb_id is not None),
            first_starts_at=starts[0],
            last_starts_at=starts[-1],
        )

    async def _merge(
        self, existing: ScreeningEvent, sighting: Sighting
    ) -> ScreeningEvent:
        """Merge a colliding sighting into the stored event and return it."""
        extracted = sighting.extracted
        existing.has_team_present = (
            existing.has_team_present or extracted.has_team_present
        )
        if existing.description is None:
            existing.description = extracted.description
        if existing.cycle_name is None:
            existing.cycle_name = extracted.cycle_name
        if existing.booking_url is None:
            existing.booking_url = sighting.booking_url
        self._session.add(existing)
        await self._session.commit()
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
        await self._session.commit()

    async def _resolve_film(self, title: str) -> Film:
        """Return the film row for an announced title, creating it if new."""
        title_key = normalize_text(title)
        statement = select(Film).where(Film.title_key == title_key)
        result = await self._session.exec(statement)
        existing = result.first()
        if existing is not None:
            return existing
        return Film(title_key=title_key, title=title)

    async def _resolve_venue(self, name: str) -> Venue:
        """Return the venue row for an announced name, creating it if new."""
        slug = normalize_text(name)
        statement = select(Venue).where(Venue.slug == slug)
        result = await self._session.exec(statement)
        existing = result.first()
        if existing is not None:
            return existing
        return Venue(slug=slug, name=name)


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
        await self._session.commit()
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
        await self._session.commit()

    async def list_active(self) -> list[Subscriber]:
        """Return every chat currently opted in to the digest.

        Returns:
            All subscribers whose ``is_active`` flag is ``True``.
        """
        statement = select(Subscriber).where(col(Subscriber.is_active).is_(True))
        result = await self._session.exec(statement)
        return list(result.all())
