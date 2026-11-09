"""Telegram bot: digest subscriptions and broadcasting.

The testable core — subscribing a chat, unsubscribing it, and broadcasting a
message — is kept as plain async functions. The aiogram wiring
(:func:`build_dispatcher`) is a thin layer that opens a database session per
update and delegates to those functions.

User-facing replies are in French (French-speaking Parisian audience; see the
project ``CLAUDE.md``). The digest and Q&A answers (``core/digest.py``,
``core/qa.py``) render clickable "Réserver" links as HTML, so every outgoing
message here uses Telegram's HTML parse mode, with link previews disabled —
otherwise a big preview card would render per link in a message that can list
several screenings at once.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import LinkPreviewOptions, Message

from lanterne.core.qa import format_qa_answer
from lanterne.io.db import Database
from lanterne.io.llm import QuestionInterpreter
from lanterne.io.repository import EventRepository, SubscriberRepository
from lanterne.logging_config import get_logger

logger = get_logger(__name__)

_SUBSCRIBED = (
    "Vous êtes abonné·e au digest hebdomadaire des séances spéciales 🎬\n"
    "Envoyez /stop pour vous désabonner."
)
_UNSUBSCRIBED = "Vous êtes désabonné·e du digest. À bientôt !"
_NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)


async def handle_subscribe(chat_id: int, repository: SubscriberRepository) -> str:
    """Subscribe a chat to the weekly digest and return the reply text.

    Args:
        chat_id: Telegram chat identifier.
        repository: Subscriber repository to record the opt-in.

    Returns:
        The confirmation message to send back.
    """
    await repository.subscribe(chat_id)
    logger.info("subscribed", extra={"ctx": {"chat_id": chat_id}})
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
    logger.info("unsubscribed", extra={"ctx": {"chat_id": chat_id}})
    return _UNSUBSCRIBED


async def handle_question(
    question: str,
    interpreter: QuestionInterpreter,
    repository: EventRepository,
    reference_date: datetime,
) -> str:
    """Answer a natural-language question about programmed screenings.

    The question is parsed into a structured filter by the LLM, run against the
    repository, and the matches are rendered as a French answer.

    Args:
        question: The user's natural-language question.
        interpreter: LLM-backed interpreter producing the query filter.
        repository: Event repository the filter is run against.
        reference_date: Instant relative time phrases are resolved against.

    Returns:
        The French answer to send back.
    """
    criteria = await interpreter.interpret(question, reference_date.date())
    events = await repository.search(criteria)
    logger.info(
        "question answered",
        extra={
            "ctx": {
                "question": question,
                "criteria": criteria.model_dump(mode="json", exclude_none=True),
                "results": len(events),
            }
        },
    )
    return format_qa_answer(events)


async def broadcast(bot: Bot, chat_ids: Sequence[int], text: str) -> int:
    """Send an HTML message to every chat, skipping (and logging) failed sends.

    Args:
        bot: The Telegram bot used to send messages.
        chat_ids: Chats to deliver the message to.
        text: The HTML-formatted message body (see ``core/digest.py``).

    Returns:
        The number of chats the message was successfully sent to.
    """
    sent = 0
    for chat_id in chat_ids:
        try:
            await bot.send_message(
                chat_id,
                text,
                parse_mode=ParseMode.HTML,
                link_preview_options=_NO_LINK_PREVIEW,
            )
        except Exception:
            logger.exception("digest send failed", extra={"ctx": {"chat_id": chat_id}})
        else:
            sent += 1
    return sent


def build_dispatcher(
    database: Database, interpreter: QuestionInterpreter
) -> Dispatcher:
    """Build the aiogram dispatcher wired to every handler.

    ``/start`` and ``/stop`` manage the digest subscription; any other text
    message is treated as a question and answered via the interpreter.

    Args:
        database: Database used to open a session per incoming update.
        interpreter: LLM-backed interpreter for natural-language questions.

    Returns:
        A dispatcher handling ``/start``, ``/stop``, and free-text questions.
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

    @router.message()
    async def on_question(message: Message) -> None:
        if not message.text:
            return
        async with database.session() as session:
            reply = await handle_question(
                message.text,
                interpreter,
                EventRepository(session),
                datetime.now(UTC),
            )
        await message.answer(
            reply, parse_mode=ParseMode.HTML, link_preview_options=_NO_LINK_PREVIEW
        )

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher
