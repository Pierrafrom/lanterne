"""Read/write access to weekly-digest subscribers."""

from datetime import UTC, datetime

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from lanterne.core.models import Subscriber
from lanterne.io.repository._commit import commit


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
        await commit(self._session)
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
        await commit(self._session)

    async def list_active(self) -> list[Subscriber]:
        """Return every chat currently opted in to the digest.

        Returns:
            All subscribers whose ``is_active`` flag is ``True``.
        """
        statement = select(Subscriber).where(col(Subscriber.is_active).is_(True))
        result = await self._session.exec(statement)
        return list(result.all())
