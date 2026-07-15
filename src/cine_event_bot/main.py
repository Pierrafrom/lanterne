"""Admin CLI entry point for cine-event-bot.

All long-running async tasks (bot polling, weekly digest cron, scraping) are
launched from here via ``asyncio.run()`` so the CLI itself stays synchronous
while the internals remain fully async.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import typer
from aiogram import Bot

from cine_event_bot.config import Settings
from cine_event_bot.core.digest import build_digest
from cine_event_bot.core.evaluation import (
    EvaluationSummary,
    evaluate_cases,
    parse_golden_cases,
)
from cine_event_bot.core.report import IngestionReport, build_admin_report
from cine_event_bot.core.specialness_evaluation import (
    evaluate_cases as evaluate_specialness_cases,
)
from cine_event_bot.core.specialness_evaluation import (
    parse_golden_cases as parse_specialness_cases,
)
from cine_event_bot.core.stats import EventStats
from cine_event_bot.io.bot import broadcast, build_dispatcher
from cine_event_bot.io.console import (
    RichReporter,
    build_progress,
    print_banner,
    print_evaluation,
    print_ingestion_summary,
    print_specialness_evaluation,
    print_stats,
)
from cine_event_bot.io.db import Database
from cine_event_bot.io.llm import build_extractor, build_interpreter
from cine_event_bot.io.repository import EventRepository, SubscriberRepository
from cine_event_bot.io.scrapers import build_scrapers
from cine_event_bot.io.scrapers.paris_cine_info import ParisCineInfoScraper
from cine_event_bot.io.tmdb import TmdbClient, build_tmdb_enricher
from cine_event_bot.pipeline import IngestionPipeline

app = typer.Typer(help="cine-event-bot admin CLI")

_HTTP_TIMEOUT_SECONDS = 30.0
_DIGEST_WINDOW = timedelta(days=7)
_RETENTION_WINDOW = timedelta(days=14)


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
    await database.migrate_to_head()
    scrapers = build_scrapers(
        build_extractor(settings),
        paris_cine_info_login=settings.paris_cine_info_login,
        paris_cine_info_password=settings.paris_cine_info_password,
    )
    try:
        async with (
            httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": "cine-event-bot"},
            ) as client,
            database.session() as session,
        ):
            repository = EventRepository(session)
            enricher = build_tmdb_enricher(client, settings.tmdb_api_key)
            pipeline = IngestionPipeline(scrapers, repository, enricher)
            with build_progress() as progress:
                report = await pipeline.run(client, RichReporter(progress))
            stats = await repository.stats()
    finally:
        await database.dispose()
    await _notify_admin(settings, report, stats)
    return report


async def _notify_admin(
    settings: Settings, report: IngestionReport, stats: EventStats
) -> None:
    """Send the run's summary to the admin chat, when one is configured."""
    if settings.admin_chat_id is None:
        return
    bot = Bot(settings.telegram_bot_token)
    try:
        await broadcast(
            bot, [settings.admin_chat_id], build_admin_report(report, stats)
        )
    finally:
        await bot.session.close()


@app.command(name="backfill-ratings")
def backfill_ratings() -> None:
    """Backfill backdrop/IMDb id and ratings for films enriched before this existed.

    One-off catch-up for ``Film`` rows that already had a TMDB match before
    ``imdb_id``/``backdrop_url``/``FilmRating`` existed (see
    ``docs/decisions/0013-film-ratings-from-paris-cine-info.md``) — the
    normal scrape only ever enriches a film once
    (``IngestionPipeline._enrich``'s ``tmdb_id is not None`` guard), so
    these rows are otherwise never revisited.
    """
    updated = asyncio.run(_run_backfill_ratings())
    typer.echo(f"Backfilled {updated} film(s) with a TMDB backdrop/IMDb id.")


async def _run_backfill_ratings() -> int:
    """Re-fetch TMDB details for every IMDb-id gap, then re-match PCI ratings."""
    settings = Settings()
    database = Database(settings.database_url)
    await database.migrate_to_head()
    try:
        async with (
            httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": "cine-event-bot"},
            ) as client,
            database.session() as session,
        ):
            repository = EventRepository(session)
            tmdb_client = TmdbClient(client, settings.tmdb_api_key)
            updated = await _backfill_tmdb_fields(repository, tmdb_client)
            await _backfill_film_ratings(settings, repository, client)
    finally:
        await database.dispose()
    return updated


async def _backfill_tmdb_fields(
    repository: EventRepository, tmdb_client: TmdbClient
) -> int:
    """Re-fetch TMDB details (backdrop/imdb id) for every film still missing one."""
    films = await repository.list_films_missing_imdb_id()
    updated = 0
    for film in films:
        if film.tmdb_id is None:
            continue
        match = await tmdb_client.get_by_id(film.tmdb_id)
        if match is None or match.imdb_id is None:
            continue
        film.imdb_id = match.imdb_id
        film.backdrop_url = match.backdrop_url
        await repository.save_film(film)
        updated += 1
    return updated


async def _backfill_film_ratings(
    settings: Settings, repository: EventRepository, client: httpx.AsyncClient
) -> None:
    """Re-run the Paris Ciné Info ratings match, when the source is configured.

    A no-op when the account credentials are not set — same "entirely
    optional" treatment as ``build_scrapers``' own Paris Ciné Info wiring
    (see ADR 0007).
    """
    if not settings.paris_cine_info_login or not settings.paris_cine_info_password:
        return
    scraper = ParisCineInfoScraper(
        build_extractor(settings),
        settings.paris_cine_info_login,
        settings.paris_cine_info_password,
    )
    ratings = await scraper.fetch_film_ratings(client)
    await repository.update_film_ratings(ratings)


@app.command()
def stats() -> None:
    """Show a summary of the events currently stored in the database."""
    print_stats(asyncio.run(_load_stats()))


async def _load_stats() -> EventStats:
    """Open the database and compute the event statistics."""
    settings = Settings()
    database = Database(settings.database_url)
    await database.migrate_to_head()
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


@app.command(name="eval-extraction")
def eval_extraction(
    dataset: Path = typer.Option(  # noqa: B008 — Typer reads options from defaults
        Path("eval/golden_extractions.json"),
        "--dataset",
        "-d",
        help="Golden dataset file.",
    ),
) -> None:
    """Score the configured LLM's extraction quality on the golden dataset."""
    settings = Settings()
    summary = asyncio.run(_run_evaluation(settings, dataset))
    print_evaluation(summary, model=settings.ollama_model)


async def _run_evaluation(settings: Settings, dataset: Path) -> EvaluationSummary:
    """Run the configured extractor over the golden dataset."""
    cases = parse_golden_cases(dataset.read_text(encoding="utf-8"))
    return await evaluate_cases(build_extractor(settings), cases)


@app.command(name="eval-specialness")
def eval_specialness(
    dataset: Path = typer.Option(  # noqa: B008 — Typer reads options from defaults
        Path("eval/golden_specialness.json"),
        "--dataset",
        "-d",
        help="Golden dataset file.",
    ),
) -> None:
    """Score the rule-based specialness classifier on the golden dataset.

    Unlike ``eval-extraction``, this has no LLM or database to await — the
    classifier and its golden cases are pure, in-memory data (see
    ``core/specialness_evaluation.py``).
    """
    cases = parse_specialness_cases(dataset.read_text(encoding="utf-8"))
    print_specialness_evaluation(evaluate_specialness_cases(cases))


@app.command(name="backup-db")
def backup_db(
    output_dir: Path = typer.Option(  # noqa: B008 — Typer reads options from defaults
        Path("backups"), "--output-dir", "-o", help="Directory for the snapshot."
    ),
) -> None:
    """Write a timestamped snapshot of the database (safe while the bot runs)."""
    target = asyncio.run(_backup_db(output_dir))
    typer.echo(f"Backup written to {target}.")


async def _backup_db(output_dir: Path) -> Path:
    """Snapshot the database into a timestamped file under ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = output_dir / f"cine-event-bot-{stamp}.db"
    database = Database(Settings().database_url)
    try:
        await database.backup_to(target)
    finally:
        await database.dispose()
    return target


@app.command(name="prune-db")
def prune_db() -> None:
    """Delete ordinary screenings older than the retention window."""
    deleted = asyncio.run(_prune_db())
    typer.echo(f"Pruned {deleted} ordinary screening(s).")


async def _prune_db() -> int:
    """Delete ordinary screenings that started before the retention window."""
    settings = Settings()
    database = Database(settings.database_url)
    await database.migrate_to_head()
    older_than = datetime.now(UTC) - _RETENTION_WINDOW
    try:
        async with database.session() as session:
            return await EventRepository(session).prune_ordinary_screenings(older_than)
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
    await database.migrate_to_head()
    now = datetime.now(UTC)
    bot = Bot(settings.telegram_bot_token)
    try:
        async with database.session() as session:
            events = await EventRepository(session).list_between(
                now, now + _DIGEST_WINDOW, only_special=True
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
    await database.migrate_to_head()
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
