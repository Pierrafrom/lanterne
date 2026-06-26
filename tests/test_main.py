"""Tests for the admin CLI entry point and logging infrastructure."""

import json
import logging
import time

import pytest
from typer.testing import CliRunner

from cine_event_bot.logging_config import JsonlFormatter, get_logger
from cine_event_bot.main import app, get_greeting
from cine_event_bot.pipeline import IngestionReport


def test_get_greeting_contains_bot_name() -> None:
    result = get_greeting()
    assert "cine-event-bot" in result


def test_get_greeting_returns_non_empty_str() -> None:
    assert len(get_greeting()) > 0


def test_greet_command_outputs_greeting() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["greet"])
    assert result.exit_code == 0
    assert "cine-event-bot" in result.output


def test_scrape_command_reports_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run() -> IngestionReport:
        return IngestionReport(events_ingested=3, sources_failed=1)

    monkeypatch.setattr("cine_event_bot.main._run_ingestion", fake_run)
    result = CliRunner().invoke(app, ["scrape"])

    assert result.exit_code == 0
    assert "3 event(s)" in result.output
    assert "1 source(s) failed" in result.output


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
