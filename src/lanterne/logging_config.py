"""Logging configuration for Lanterne.

Two sinks per log event:

- a **JSONL file** (``logs/app.jsonl``) — machine/AI-friendly, one JSON object
  per line, for cheap ``grep``/``jq`` debugging;
- a **human-readable console** stream via Rich — colored, with the structured
  ``ctx`` rendered as ``key=value`` pairs, so a developer can follow what the
  bot is doing live.

Usage in application code::

    from lanterne.logging_config import get_logger
    logger = get_logger(__name__)
    logger.error("scraping failed", extra={"ctx": {"source": "mk2.fr", "url": "..."}})
"""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

LOG_DIR = Path("logs")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Shared console so Rich log lines and progress bars coexist without clobbering.
console = Console()


class JsonlFormatter(logging.Formatter):
    """Serializes each log record into one JSON line.

    Fixed fields: ts, level, module, msg, ctx — see rules/common/logging.md.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log record to a single JSON line.

        Args:
            record: The log record to format.

        Returns:
            A JSON string with fixed fields ts, level, module, msg, ctx, and an
            ``exc`` field carrying the traceback when one is attached (e.g. from
            ``logger.exception``).
        """
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
            "ctx": getattr(record, "ctx", {}),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class ConsoleFormatter(logging.Formatter):
    """Renders a record as ``message (key=value, ...)`` for the console.

    The structured ``ctx`` is appended as readable key=value pairs; Rich adds
    the timestamp, level, and color around it.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Append the ``ctx`` key=value pairs to the message.

        Args:
            record: The log record to format.

        Returns:
            The message, followed by its context as ``(k=v, ...)`` when present.
        """
        message = record.getMessage()
        ctx = getattr(record, "ctx", {})
        if not ctx:
            return message
        pairs = ", ".join(f"{key}={value}" for key, value in ctx.items())
        return f"{message} ({pairs})"


def get_logger(name: str) -> logging.Logger:
    """Return a logger writing JSONL to file and human-readable text to console.

    Idempotent — returns the same logger if called multiple times with the same
    name. The level comes from the ``LOG_LEVEL`` env var (default ``INFO``).

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A Logger instance with the JSONL file and Rich console handlers attached.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(LOG_LEVEL)

    LOG_DIR.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(LOG_DIR / "app.jsonl", encoding="utf-8")
    file_handler.setFormatter(JsonlFormatter())
    logger.addHandler(file_handler)

    console_handler = RichHandler(
        console=console,
        show_path=False,
        rich_tracebacks=True,
        markup=False,
    )
    console_handler.setFormatter(ConsoleFormatter())
    logger.addHandler(console_handler)

    return logger
