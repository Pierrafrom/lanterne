"""Typed application settings, loaded from environment variables or ``.env``.

A single ``Settings`` instance is the source of truth for every external
credential and connection string used across the bot, scraper, and CLI. Field
names map case-insensitively to the variables documented in ``.env.example``
(e.g. ``telegram_bot_token`` ← ``TELEGRAM_BOT_TOKEN``).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration resolved from the environment.

    Required fields have no default and raise a ``ValidationError`` at
    construction time if absent — failing fast rather than letting the bot
    start with a missing token.

    Attributes:
        telegram_bot_token: Bot token issued by @BotFather.
        tmdb_api_key: TMDB API key (v3 auth) for film enrichment.
        ollama_base_url: Base URL of the Ollama instance (OpenAI-compatible).
        ollama_model: Model name used for structured event extraction.
        database_url: Async SQLAlchemy URL for the SQLite database.
        log_level: Logging verbosity (DEBUG, INFO, WARNING, ERROR).
        admin_chat_id: Telegram chat that receives the post-scrape report;
            no report is sent when unset.
    """

    telegram_bot_token: str
    tmdb_api_key: str
    ollama_base_url: str
    ollama_model: str
    database_url: str = "sqlite+aiosqlite:///./cine_event_bot.db"
    log_level: str = "INFO"
    admin_chat_id: int | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )
