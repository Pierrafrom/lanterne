"""Admin CLI entry point for cine-event-bot.

All long-running async tasks (bot polling, weekly digest cron, scraping) are
launched from here via ``asyncio.run()`` so the CLI itself stays synchronous
while the internals remain fully async.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import typer
from aiogram import Bot

from cine_event_bot.config import Settings
from cine_event_bot.core.digest import build_digest
from cine_event_bot.io.bot import broadcast, build_dispatcher
from cine_event_bot.io.console import (
    RichReporter,
    build_progress,
    print_banner,
    print_ingestion_summary,
    print_stats,
)
from cine_event_bot.io.db import Database
from cine_event_bot.io.llm import build_extractor, build_interpreter
from cine_event_bot.io.repository import (
    EventRepository,
    EventStats,
    SubscriberRepository,
)
from cine_event_bot.io.scrapers import build_scrapers
from cine_event_bot.io.tmdb import build_tmdb_enricher
from cine_event_bot.pipeline import IngestionPipeline, IngestionReport

app = typer.Typer(help="cine-event-bot admin CLI")

_HTTP_TIMEOUT_SECONDS = 30.0
_DIGEST_WINDOW = timedelta(days=7)


def get_greeting() -> str:
    """Return the bot startup greeting.

    Returns:
        A short status message confirming the bot package is importable.
    """
    return "cine-event-bot is ready — tracking special screenings in Paris/IDF."


@app.command()
def greet() -> None:
    """Print the startup greeting (smoke-test that the install works)."""
    typer.echo(get_greeting())


@app.command()
def scrape() -> None:
    """Scrape every source and upsert deduplicated events into the database."""
    report = asyncio.run(_run_ingestion())
    print_ingestion_summary(report)


async def _run_ingestion() -> IngestionReport:
    """Wire settings, database, scrapers, and pipeline for one scrape run."""
    settings = Settings()
    database = Database(settings.database_url)
    await database.create_tables()
    scrapers = build_scrapers(build_extractor(settings))
    try:
        async with (
            httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": "cine-event-bot"},
            ) as client,
            database.session() as session,
        ):
            enricher = build_tmdb_enricher(client, settings.tmdb_api_key)
            pipeline = IngestionPipeline(scrapers, EventRepository(session), enricher)
            with build_progress() as progress:
                return await pipeline.run(client, RichReporter(progress))
    finally:
        await database.dispose()


@app.command()
def stats() -> None:
    """Show a summary of the events currently stored in the database."""
    print_stats(asyncio.run(_load_stats()))


async def _load_stats() -> EventStats:
    """Open the database and compute the event statistics."""
    settings = Settings()
    database = Database(settings.database_url)
    await database.create_tables()
    try:
        async with database.session() as session:
            return await EventRepository(session).stats()
    finally:
        await database.dispose()


@app.command(name="reset-db")
def reset_db(
    yes: bool = typer.Option(  # noqa: B008 — Typer reads options from defaults
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
) -> None:
    """Drop and recreate all tables — deletes every stored event and subscriber."""
    if not yes and not typer.confirm("This deletes ALL stored data. Continue?"):
        typer.echo("Aborted.")
        return
    asyncio.run(_reset_db())
    typer.echo("Database reset.")


async def _reset_db() -> None:
    """Drop and recreate the database schema."""
    database = Database(Settings().database_url)
    try:
        await database.reset_tables()
    finally:
        await database.dispose()


@app.command(name="weekly-digest")
def weekly_digest() -> None:
    """Send the upcoming week's digest to every active subscriber."""
    sent = asyncio.run(_run_weekly_digest())
    typer.echo(f"Digest sent to {sent} subscriber(s).")


async def _run_weekly_digest() -> int:
    """Build the next-7-days digest and broadcast it to active subscribers."""
    settings = Settings()
    database = Database(settings.database_url)
    await database.create_tables()
    now = datetime.now(UTC)
    bot = Bot(settings.telegram_bot_token)
    try:
        async with database.session() as session:
            events = await EventRepository(session).list_between(
                now, now + _DIGEST_WINDOW
            )
            subscribers = await SubscriberRepository(session).list_active()
        chat_ids = [subscriber.chat_id for subscriber in subscribers]
        return await broadcast(bot, chat_ids, build_digest(events))
    finally:
        await bot.session.close()
        await database.dispose()


@app.command(name="run-bot")
def run_bot() -> None:
    """Start the Telegram bot, handling /start and /stop subscriptions."""
    asyncio.run(_run_bot())


async def _run_bot() -> None:
    """Wire the database and dispatcher, then poll Telegram until stopped."""
    settings = Settings()
    database = Database(settings.database_url)
    await database.create_tables()
    bot = Bot(settings.telegram_bot_token)
    dispatcher = build_dispatcher(database, build_interpreter(settings))
    print_banner("cine-event-bot is polling Telegram - press Ctrl-C to stop")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await database.dispose()


if __name__ == "__main__":  # pragma: no cover
    app()
