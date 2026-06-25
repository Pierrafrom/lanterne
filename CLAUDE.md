# cine-event-bot — project context for AI assistants

> Global rules (clean code, TDD, typing, uv, logging, git workflow) are in
> `~/.claude/CLAUDE.md` — this file only adds what is specific to this project.

## What this project does

Auto-hosted Telegram bot that aggregates **special screenings** in Paris/IDF
(avant-premières with team, cine-concerts, retrospectives, open-air) from four
scraped sources, sends a weekly digest, and answers natural-language questions
about programmed films using TMDB data.

## Architecture

Fully async pipeline from end to end — aiogram event loop drives everything:

```
aiogram (event loop)
   ├── httpx.AsyncClient → TMDB API
   ├── Instructor (async) → Ollama → structured event extraction
   └── SQLModel (async session, aiosqlite) → read/write events

Weekly cron (Typer CLI, asyncio.run())
   ├── httpx.AsyncClient → scrape 4 sources
   ├── Instructor (async) → Ollama → structure events
   └── SQLModel (async session) → insert + deduplication
```

## Package structure

```
src/cine_event_bot/
├── main.py              # Typer admin CLI (sync entry point, wraps asyncio.run)
├── logging_config.py    # JSONL logger — call get_logger(__name__) everywhere
├── core/                # business logic (models, digest, dedup)
└── io/                  # external I/O (scraper, tmdb client, bot handlers)
```

## Key technical decisions

- **Async throughout**: aiogram, httpx, aiosqlite, Instructor async client — do not
  introduce sync blocking calls inside async functions.
- **Instructor over raw LLM calls**: use `instructor.from_openai(AsyncOpenAI(...))` with
  the Ollama-compatible OpenAI endpoint (`/v1`) for structured Pydantic output.
- **SQLModel**: combines SQLAlchemy ORM + Pydantic validation — use `async_session`
  from `sqlalchemy.ext.asyncio`, never sync sessions.
- **Typer CLI stays sync**: the admin CLI (`main.py`) uses `asyncio.run()` to bridge
  sync CLI commands to async bot/scraper logic — intentional design choice.

## Environment variables

See `.env.example` for the full list. Required to run:

- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TMDB_API_KEY` — from themoviedb.org
- `OLLAMA_BASE_URL` + `OLLAMA_MODEL` — local or remote Ollama instance

## Running locally

```fish
uv sync --all-groups
cp .env.example .env  # fill in the required tokens
uv run python -m cine_event_bot  # start the bot
uv run pytest         # run tests
```
