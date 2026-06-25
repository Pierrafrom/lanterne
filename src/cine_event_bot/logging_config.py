"""JSONL logging configuration for cine-event-bot.

Usage in application code::

    from cine_event_bot.logging_config import get_logger
    logger = get_logger(__name__)
    logger.error("scraping failed", extra={"ctx": {"source": "mk2.fr", "url": "..."}})

Output format (one JSON line per event)::

    {"ts": "...", "level": "ERROR", "module": "...", "msg": "...", "ctx": {...}}
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

LOG_DIR = Path("logs")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


class JsonlFormatter(logging.Formatter):
    """Serializes each log record into one JSON line.

    Fixed fields: ts, level, module, msg, ctx — see rules/common/logging.md.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log record to a single JSON line.

        Args:
            record: The log record to format.

        Returns:
            A JSON string with fixed fields ts, level, module, msg, ctx.
        """
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
            "ctx": getattr(record, "ctx", {}),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger: JSONL file + stdout, level via LOG_LEVEL env var.

    Idempotent — returns the same logger if called multiple times with the same name.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A Logger instance with JSONL file and stdout handlers attached.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(LOG_LEVEL)

    LOG_DIR.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(LOG_DIR / "app.jsonl", encoding="utf-8")
    file_handler.setFormatter(JsonlFormatter())
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(JsonlFormatter())
    logger.addHandler(stream_handler)

    return logger
