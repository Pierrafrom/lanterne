"""Tests for the typed application settings."""

import pytest
from pydantic import ValidationError

from lanterne.config import Settings

_REQUIRED_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "TMDB_API_KEY",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "DATABASE_URL",
    "LOG_LEVEL",
)


def test_settings_reads_explicit_values() -> None:
    settings = Settings(
        telegram_bot_token="token",  # noqa: S106 — test fixture, not a real secret
        tmdb_api_key="tmdb",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.2",
        database_url="sqlite+aiosqlite:///./test.db",
    )

    assert settings.telegram_bot_token == "token"
    assert settings.ollama_model == "llama3.2"


def test_settings_default_log_level_is_info() -> None:
    settings = Settings(
        telegram_bot_token="token",  # noqa: S106 — test fixture
        tmdb_api_key="tmdb",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.2",
        database_url="sqlite+aiosqlite:///./test.db",
    )

    assert settings.log_level == "INFO"


def test_settings_missing_required_field_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _REQUIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
