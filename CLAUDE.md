# cine-event-bot — project context for AI assistants

> Global rules (clean code, TDD, typing, uv, logging, git workflow) are in
> `~/.claude/CLAUDE.md` — this file only adds what is specific to this project.

## What this project does

Auto-hosted Telegram bot that aggregates **special screenings** in Paris/IDF
(avant-premières with team, cine-concerts, retrospectives, open-air,
festivals, cult screenings, ciné-clubs, short-film programmes) from nine
scraped sources (Première Projo, MK2, La Cinémathèque française, Le Forum
des images, Le Champo, Le Louxor, la Fondation Jérôme Seydoux-Pathé,
La Villette, Paris Ciné Info — the last an optional, authenticated source,
see [ADR 0007](docs/decisions/0007-paris-cine-info.md); see also
`docs/scraping-strategy.md`), sends a weekly digest, and answers
natural-language questions about programmed films using TMDB data.

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

## Language of user-facing strings

Code, identifiers, comments, docstrings, logs and docs are in English (global
rule). The **exception** is end-user-facing Telegram text — bot replies
(`/start`, `/stop`) and the weekly digest — which is in **French**, since the
bot targets a French-speaking Parisian audience. Digest times are shown in
Paris local time (events are stored in UTC).

## Running locally

```fish
uv sync --all-groups
cp .env.example .env   # fill in the required tokens
uv run pytest          # run tests

# Admin CLI (Typer) via the `cine-event-bot` entry point — see `--help`
uv run cine-event-bot scrape          # scrape + enrich + persist (idempotent)
uv run cine-event-bot stats           # summary of stored events
uv run cine-event-bot weekly-digest   # broadcast the week's digest
uv run cine-event-bot backup-db       # timestamped snapshot into ./backups/
uv run cine-event-bot run-bot         # start the Telegram bot
uv run cine-event-bot reset-db --yes  # drop + recreate all tables (wipes data)
```
