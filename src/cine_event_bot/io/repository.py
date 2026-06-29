"""Repositories mediating access to screening events and subscribers.

Each repository wraps an injected :class:`AsyncSession` and owns the commit for
its write operations, so callers work in terms of domain intent
(``add`` an event, ``subscribe`` a chat) rather than session mechanics.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from cine_event_bot.core.models import ScreeningEvent, Subscriber
from cine_event_bot.core.qa import QueryCriteria

_SEARCH_LIMIT = 20


@dataclass(frozen=True, slots=True)
class EventStats:
    """A snapshot of the stored events, for the ``stats`` command.

    Attributes:
        total: Number of events stored.
        by_source: Event count per source domain.
        by_type: Event count per event type.
        team_present: Number of events with the film team present.
        enriched: Number of events enriched with a TMDB id.
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

    async def add(self, event: ScreeningEvent) -> ScreeningEvent:
        """Persist a new event and return it with its assigned primary key.

        Args:
            event: The event to insert.

        Returns:
            The same instance, refreshed with its database-assigned ``id``.
        """
        self._session.add(event)
        await self._session.commit()
        await self._session.refresh(event)
        return event

    async def upsert(self, event: ScreeningEvent) -> ScreeningEvent:
        """Insert an event, or merge it into the existing one on key collision.

        Implements the cross-source deduplication strategy (see
        ``docs/decisions/0002-dedup-merge-strategy.md``): the first source to
        report a screening owns its provenance and identity; later sources only
        enrich it. Specifically, on collision the stored row keeps its ``id``,
        ``source``, ``source_url`` and any TMDB enrichment, while
        ``has_team_present`` becomes the logical OR of both reports and a
        missing ``description`` is backfilled from the newcomer.

        Args:
            event: The freshly built event to insert or merge.

        Returns:
            The persisted event — newly inserted or the enriched existing row.
        """
        existing = await self.get_by_dedup_key(event.dedup_key)
        if existing is None:
            return await self.add(event)

        existing.has_team_present = existing.has_team_present or event.has_team_present
        if existing.description is None:
            existing.description = event.description
        self._session.add(existing)
        await self._session.commit()
        await self._session.refresh(existing)
        return existing

    async def get_by_dedup_key(self, dedup_key: str) -> ScreeningEvent | None:
        """Return the event matching a deduplication key, if any.

        Args:
            dedup_key: The unique deduplication key to look up.

        Returns:
            The matching event, or ``None`` when no event has that key.
        """
        statement = select(ScreeningEvent).where(ScreeningEvent.dedup_key == dedup_key)
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
            Matching events ordered by ascending start time.
        """
        statement = (
            select(ScreeningEvent)
            .where(col(ScreeningEvent.starts_at) >= start)
            .where(col(ScreeningEvent.starts_at) < end)
            .order_by(col(ScreeningEvent.starts_at))
        )
        result = await self._session.exec(statement)
        return list(result.all())

    async def search(self, criteria: QueryCriteria) -> list[ScreeningEvent]:
        """Return events matching a structured query, soonest first.

        Each set criterion narrows the result; an unset criterion imposes no
        constraint. Text matches the title or synopsis case-insensitively.

        Args:
            criteria: The structured filter to apply.

        Returns:
            Matching events ordered by ascending start time, capped in size.
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
            statement = statement.where(
                or_(
                    col(ScreeningEvent.title).ilike(pattern),
                    col(ScreeningEvent.overview).ilike(pattern),
                )
            )
        statement = statement.order_by(col(ScreeningEvent.starts_at)).limit(
            _SEARCH_LIMIT
        )
        result = await self._session.exec(statement)
        return list(result.all())

    async def stats(self) -> EventStats:
        """Compute a summary snapshot of the stored events.

        Returns:
            An :class:`EventStats` with totals, breakdowns, and the date range.
        """
        result = await self._session.exec(select(ScreeningEvent))
        events = list(result.all())
        if not events:
            return EventStats(total=0)
        starts = sorted(event.starts_at for event in events)
        return EventStats(
            total=len(events),
            by_source=dict(Counter(event.source.value for event in events)),
            by_type=dict(Counter(event.event_type.value for event in events)),
            team_present=sum(1 for event in events if event.has_team_present),
            enriched=sum(1 for event in events if event.tmdb_id is not None),
            first_starts_at=starts[0],
            last_starts_at=starts[-1],
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
