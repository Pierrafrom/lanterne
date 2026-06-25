"""Admin CLI entry point for cine-event-bot.

All long-running async tasks (bot polling, weekly digest cron, scraping) are
launched from here via ``asyncio.run()`` so the CLI itself stays synchronous
while the internals remain fully async.
"""

import asyncio

import httpx
import typer

from cine_event_bot.config import Settings
from cine_event_bot.io.db import Database
from cine_event_bot.io.llm import build_extractor
from cine_event_bot.io.repository import EventRepository
from cine_event_bot.io.scrapers import build_scrapers
from cine_event_bot.pipeline import IngestionPipeline, IngestionReport

app = typer.Typer(help="cine-event-bot admin CLI")

_HTTP_TIMEOUT_SECONDS = 30.0


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
    typer.echo(
        f"Ingested {report.events_ingested} event(s); "
        f"{report.sources_failed} source(s) failed."
    )


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
            pipeline = IngestionPipeline(scrapers, EventRepository(session))
            return await pipeline.run(client)
    finally:
        await database.dispose()


if __name__ == "__main__":  # pragma: no cover
    app()
