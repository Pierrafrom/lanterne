"""Tests for the admin CLI entry point and logging infrastructure."""

import logging
import time

from typer.testing import CliRunner

from cine_event_bot.logging_config import get_logger
from cine_event_bot.main import app, get_greeting


def test_get_greeting_contains_bot_name() -> None:
    result = get_greeting()
    assert "cine-event-bot" in result


def test_get_greeting_returns_non_empty_str() -> None:
    assert len(get_greeting()) > 0


def test_greet_command_outputs_greeting() -> None:
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "cine-event-bot" in result.output


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
