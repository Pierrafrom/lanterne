"""Tests for the bot subscription, question, and broadcasting handlers."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from aiogram.enums import ParseMode
from factories import make_sighting
from sqlmodel.ext.asyncio.session import AsyncSession

from lanterne.core.models import EventType, Source
from lanterne.core.qa import QueryCriteria
from lanterne.io.bot import (
    broadcast,
    build_dispatcher,
    handle_question,
    handle_subscribe,
    handle_unsubscribe,
)
from lanterne.io.db import Database
from lanterne.io.repository import EventRepository, SubscriberRepository


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


async def test_broadcast_sends_as_html_with_link_previews_disabled() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await broadcast(bot, [1], "digest")

    _, kwargs = bot.send_message.await_args
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert kwargs["link_preview_options"].is_disabled is True


async def test_broadcast_skips_failed_sends() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=[None, RuntimeError("blocked"), None])

    sent = await broadcast(bot, [1, 2, 3], "digest")

    assert sent == 2


async def test_handle_question_interprets_searches_and_formats(
    session: AsyncSession,
) -> None:
    repository = EventRepository(session)
    await repository.ingest(
        make_sighting(
            title="Le Voyage de Chihiro",
            event_type=EventType.RETROSPECTIVE,
            venue="La Cinémathèque française",
            starts_at=datetime(2026, 7, 7, 18, 0, tzinfo=UTC),
            source=Source.CINEMATHEQUE,
        )
    )
    interpreter = MagicMock()
    interpreter.interpret = AsyncMock(return_value=QueryCriteria(text_query="chihiro"))

    answer = await handle_question(
        "Y a-t-il du Miyazaki ?",
        interpreter,
        repository,
        datetime(2026, 6, 25, tzinfo=UTC),
    )

    assert "Le Voyage de Chihiro" in answer
    interpreter.interpret.assert_awaited_once()


def test_build_dispatcher_returns_a_dispatcher() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")

    dispatcher = build_dispatcher(database, MagicMock())

    assert dispatcher is not None
