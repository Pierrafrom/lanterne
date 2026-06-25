"""Tests for the bot subscription handlers and broadcasting."""

from unittest.mock import AsyncMock, MagicMock

from sqlmodel.ext.asyncio.session import AsyncSession

from cine_event_bot.io.bot import (
    broadcast,
    build_dispatcher,
    handle_subscribe,
    handle_unsubscribe,
)
from cine_event_bot.io.db import Database
from cine_event_bot.io.repository import SubscriberRepository


async def test_handle_subscribe_records_and_confirms(session: AsyncSession) -> None:
    repository = SubscriberRepository(session)

    reply = await handle_subscribe(42, repository)

    assert "abonné" in reply
    assert [sub.chat_id for sub in await repository.list_active()] == [42]


async def test_handle_unsubscribe_removes_and_confirms(session: AsyncSession) -> None:
    repository = SubscriberRepository(session)
    await handle_subscribe(42, repository)

    reply = await handle_unsubscribe(42, repository)

    assert "désabonné" in reply
    assert await repository.list_active() == []


async def test_broadcast_sends_to_every_chat() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock()

    sent = await broadcast(bot, [1, 2, 3], "digest")

    assert sent == 3
    assert bot.send_message.await_count == 3


async def test_broadcast_skips_failed_sends() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[None, RuntimeError("blocked"), None])

    sent = await broadcast(bot, [1, 2, 3], "digest")

    assert sent == 2


def test_build_dispatcher_returns_a_dispatcher() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")

    dispatcher = build_dispatcher(database)

    assert dispatcher is not None
