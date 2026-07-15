"""Tests for the admin CLI entry point and logging infrastructure."""

import json
import logging
import time
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import lanterne.main as main_module
from lanterne.config import Settings
from lanterne.core.models import Film, RatingSource
from lanterne.core.report import IngestionReport, SourceOutcome
from lanterne.core.stats import EventStats
from lanterne.io.repository import EventRepository
from lanterne.io.scrapers.base import RatingRecord
from lanterne.io.scrapers.paris_cine_info import ParisCineInfoScraper
from lanterne.io.tmdb import MovieMatch
from lanterne.logging_config import JsonlFormatter, get_logger
from lanterne.main import app, get_greeting


def test_get_greeting_contains_bot_name() -> None:
    result = get_greeting()
    assert "Lanterne" in result


def test_get_greeting_returns_non_empty_str() -> None:
    assert len(get_greeting()) > 0


def test_greet_command_outputs_greeting() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["greet"])
    assert result.exit_code == 0
    assert "Lanterne" in result.output


def test_scrape_command_reports_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run() -> IngestionReport:
        return IngestionReport(
            outcomes=(
                SourceOutcome(source="premiereprojo.fr", events=3),
                SourceOutcome(source="cinematheque.fr", events=None),
            )
        )

    monkeypatch.setattr("lanterne.main._run_ingestion", fake_run)
    result = CliRunner().invoke(app, ["scrape"])

    assert result.exit_code == 0
    assert "3 event(s)" in result.output
    assert "1 source(s) failed" in result.output


def test_stats_command_prints_total(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_stats() -> EventStats:
        return EventStats(total=169, by_source={"premiereprojo.fr": 169})

    monkeypatch.setattr("lanterne.main._load_stats", fake_stats)
    result = CliRunner().invoke(app, ["stats"])

    assert result.exit_code == 0
    assert "169" in result.output


def test_eval_specialness_command_scores_the_golden_dataset() -> None:
    # No mocking needed: the classifier and dataset are pure, in-memory data.
    result = CliRunner().invoke(app, ["eval-specialness"])

    assert result.exit_code == 0
    assert "Accuracy" in result.output


def test_prune_db_command_reports_count(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_prune() -> int:
        return 42

    monkeypatch.setattr("lanterne.main._prune_db", fake_prune)
    result = CliRunner().invoke(app, ["prune-db"])

    assert result.exit_code == 0
    assert "42" in result.output


def test_backfill_ratings_command_reports_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_backfill() -> int:
        return 7

    monkeypatch.setattr("lanterne.main._run_backfill_ratings", fake_backfill)
    result = CliRunner().invoke(app, ["backfill-ratings"])

    assert result.exit_code == 0
    assert "7" in result.output


async def test_backfill_tmdb_fields_updates_films_missing_imdb_id(
    session,  # noqa: ANN001
) -> None:
    repo = EventRepository(session)
    session.add(Film(title_key="dune", title="Dune", tmdb_id=42))
    await session.commit()

    class _FakeTmdbClient:
        async def get_by_id(self, tmdb_id: int) -> MovieMatch:
            return MovieMatch(
                tmdb_id=tmdb_id,
                imdb_id="tt0000042",
                original_title=None,
                director=None,
                release_year=None,
                runtime_minutes=None,
                genres=None,
                overview=None,
                poster_url=None,
                backdrop_url="https://image.tmdb.org/t/p/w1280/b.jpg",
                vote_average=None,
            )

    updated = await main_module._backfill_tmdb_fields(repo, _FakeTmdbClient())

    assert updated == 1
    assert await repo.list_films_missing_imdb_id() == []


async def test_backfill_tmdb_fields_skips_a_film_tmdb_no_longer_has(
    session,  # noqa: ANN001
) -> None:
    repo = EventRepository(session)
    session.add(Film(title_key="dune", title="Dune", tmdb_id=42))
    await session.commit()

    class _NotFoundTmdbClient:
        async def get_by_id(self, tmdb_id: int) -> None:  # noqa: ARG002
            return None

    updated = await main_module._backfill_tmdb_fields(repo, _NotFoundTmdbClient())

    assert updated == 0


async def test_backfill_film_ratings_skips_when_not_configured(
    session,  # noqa: ANN001
) -> None:
    settings = Settings(
        telegram_bot_token="t",
        tmdb_api_key="k",
        ollama_base_url="http://localhost",
        ollama_model="m",
        # Explicit None: overrides any PARIS_CINE_INFO_* set in the real
        # .env this test suite otherwise inherits.
        paris_cine_info_login=None,
        paris_cine_info_password=None,
    )
    repo = EventRepository(session)

    # Should not raise, and should not attempt to build an extractor/scraper.
    await main_module._backfill_film_ratings(settings, repo, client=MagicMock())


async def test_backfill_film_ratings_applies_ratings_when_configured(
    session,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        telegram_bot_token="t",
        tmdb_api_key="k",
        ollama_base_url="http://localhost",
        ollama_model="m",
        paris_cine_info_login="user@example.test",
        paris_cine_info_password="secret",
    )
    repo = EventRepository(session)
    applied: dict[str, list[RatingRecord]] = {}

    async def fake_fetch_film_ratings(
        self: ParisCineInfoScraper,  # noqa: ARG001
        client: object,  # noqa: ARG001
    ) -> dict[str, list[RatingRecord]]:
        return {"tt0000001": [RatingRecord(source=RatingSource.IMDB, rating=7.0)]}

    async def fake_update_film_ratings(
        self: EventRepository,  # noqa: ARG001
        ratings: dict[str, list[RatingRecord]],
    ) -> None:
        applied.update(ratings)

    monkeypatch.setattr(
        ParisCineInfoScraper, "fetch_film_ratings", fake_fetch_film_ratings
    )
    monkeypatch.setattr(
        EventRepository, "update_film_ratings", fake_update_film_ratings
    )
    monkeypatch.setattr(
        main_module,
        "build_extractor",
        lambda settings: MagicMock(),  # noqa: ARG005
    )

    await main_module._backfill_film_ratings(settings, repo, client=MagicMock())

    assert "tt0000001" in applied


def test_reset_db_command_runs_with_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_reset() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("lanterne.main._reset_db", fake_reset)
    result = CliRunner().invoke(app, ["reset-db", "--yes"])

    assert result.exit_code == 0
    assert called is True
    assert "reset" in result.output.lower()


def test_reset_db_command_aborts_without_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_reset() -> None:
        raise AssertionError("reset must not run when declined")

    monkeypatch.setattr("lanterne.main._reset_db", fail_reset)
    result = CliRunner().invoke(app, ["reset-db"], input="n\n")

    assert "Aborted" in result.output


def test_get_logger_returns_logger() -> None:
    logger = get_logger("test.module")
    assert isinstance(logger, logging.Logger)


def test_get_logger_idempotent() -> None:
    logger1 = get_logger("test.idempotent")
    logger2 = get_logger("test.idempotent")
    assert logger1 is logger2


def test_logger_emits_without_error() -> None:
    unique = f"test.emit.{time.monotonic_ns()}"
    logger = get_logger(unique)
    logger.info("scaffold smoke test", extra={"ctx": {"stage": "init"}})


def test_jsonl_formatter_includes_exception_traceback() -> None:
    record = logging.LogRecord(
        name="test.exc",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="boom",
        args=(),
        exc_info=None,
    )
    try:
        raise ValueError("kaboom")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()
    payload = json.loads(JsonlFormatter().format(record))

    assert payload["msg"] == "boom"
    assert "kaboom" in payload["exc"]


def test_jsonl_formatter_omits_exc_without_exception() -> None:
    record = logging.LogRecord(
        name="test.noexc",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ok",
        args=(),
        exc_info=None,
    )
    payload = json.loads(JsonlFormatter().format(record))

    assert "exc" not in payload
