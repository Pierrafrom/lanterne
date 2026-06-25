"""Repositories mediating access to screening events and subscribers.

Each repository wraps an injected :class:`AsyncSession` and owns the commit for
its write operations, so callers work in terms of domain intent
(``add`` an event, ``subscribe`` a chat) rather than session mechanics.
"""

from datetime import UTC, datetime

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from cine_event_bot.core.models import ScreeningEvent, Subscriber


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
