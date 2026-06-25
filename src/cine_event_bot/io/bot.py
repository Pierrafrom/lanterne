"""Telegram bot: digest subscriptions and broadcasting.

The testable core — subscribing a chat, unsubscribing it, and broadcasting a
message — is kept as plain async functions. The aiogram wiring
(:func:`build_dispatcher`) is a thin layer that opens a database session per
update and delegates to those functions.

User-facing replies are in French (French-speaking Parisian audience; see the
project ``CLAUDE.md``).
"""

from collections.abc import Sequence

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from cine_event_bot.io.db import Database
from cine_event_bot.io.repository import SubscriberRepository
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)

_SUBSCRIBED = (
    "Vous êtes abonné·e au digest hebdomadaire des séances spéciales 🎬\n"
    "Envoyez /stop pour vous désabonner."
)
_UNSUBSCRIBED = "Vous êtes désabonné·e du digest. À bientôt !"


async def handle_subscribe(chat_id: int, repository: SubscriberRepository) -> str:
    """Subscribe a chat to the weekly digest and return the reply text.

    Args:
        chat_id: Telegram chat identifier.
        repository: Subscriber repository to record the opt-in.

    Returns:
        The confirmation message to send back.
    """
    await repository.subscribe(chat_id)
    return _SUBSCRIBED


async def handle_unsubscribe(chat_id: int, repository: SubscriberRepository) -> str:
    """Unsubscribe a chat from the digest and return the reply text.

    Args:
        chat_id: Telegram chat identifier.
        repository: Subscriber repository to record the opt-out.

    Returns:
        The confirmation message to send back.
    """
    await repository.unsubscribe(chat_id)
    return _UNSUBSCRIBED


async def broadcast(bot: Bot, chat_ids: Sequence[int], text: str) -> int:
    """Send a message to every chat, skipping (and logging) failed sends.

    Args:
        bot: The Telegram bot used to send messages.
        chat_ids: Chats to deliver the message to.
        text: The message body.

    Returns:
        The number of chats the message was successfully sent to.
    """
    sent = 0
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logger.exception("digest send failed", extra={"ctx": {"chat_id": chat_id}})
        else:
            sent += 1
    return sent


def build_dispatcher(database: Database) -> Dispatcher:
    """Build the aiogram dispatcher wired to the subscription handlers.

    Args:
        database: Database used to open a session per incoming update.

    Returns:
        A dispatcher handling ``/start`` and ``/stop``.
    """
    router = Router()

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        async with database.session() as session:
            reply = await handle_subscribe(
                message.chat.id, SubscriberRepository(session)
            )
        await message.answer(reply)

    @router.message(Command("stop"))
    async def on_stop(message: Message) -> None:
        async with database.session() as session:
            reply = await handle_unsubscribe(
                message.chat.id, SubscriberRepository(session)
            )
        await message.answer(reply)

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher
