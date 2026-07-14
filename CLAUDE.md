# cine-event-bot — project context for AI assistants

> Global rules (clean code, TDD, typing, uv, logging, git workflow) are in
> `~/.claude/CLAUDE.md` — this file only adds what is specific to this project.

## What this project does

Auto-hosted Telegram bot for cinema screenings in Paris/IDF: a weekly digest
of curated **special screenings** (avant-premières with team, cine-concerts,
retrospectives, open-air, festivals, cult screenings, ciné-clubs, short-film
programmes), and a natural-language Q&A over every stored screening using
TMDB data.

The database is being expanded from "special screenings only" to "every
screening, with specialness detected after ingestion" — see
[ADR 0008](docs/decisions/0008-drop-allocine-width-source.md) and
[`docs/coverage-matrix.md`](docs/coverage-matrix.md) for the sourcing
strategy and rollout plan. Seven scrapers are wired in: five venue-specific
sources (Première Projo, La Cinémathèque française, Le Forum des images, la
Fondation Jérôme Seydoux-Pathé, La Villette — MK2, Le Louxor, and Le Champo
were **retired** in favor of confirmed Paris Ciné Info coverage; see
[ADR 0011](docs/decisions/0011-retire-mk2-louxor.md) and
[ADR 0012](docs/decisions/0012-retire-lechampo.md)) still only report
special screenings, plus two **width sources** covering every screening at
their venues: Paris Ciné Info (Paris intra-muros, optional/authenticated —
see [ADR 0007](docs/decisions/0007-paris-cine-info.md)) and offi.fr (the
Île-de-France suburbs). A width-source screening starts `is_special=False`
and is upgraded by the rule-based specialness classifier
(`core/specialness.py`, run as a single end-of-run batch pass over every
currently-ordinary screening — see
[ADR 0009](docs/decisions/0009-specialness-rules-only.md)) when team
presence, a cycle name, an institution venue, or film age/rarity signals
it; every classified screening's verdict is also logged as structured
JSONL for building a real evaluation dataset over time. `prune-db` deletes
ordinary screenings older than 14 days (never a special one) — schedule it
after `scrape` in the weekly cron. See also `docs/scraping-strategy.md`.

## Architecture

Fully async pipeline from end to end — aiogram event loop drives everything:

```
aiogram (event loop)
   ├── httpx.AsyncClient → TMDB API
   ├── Instructor (async) → Ollama → structured event extraction
   └── SQLModel (async session, aiosqlite) → read/write events

Weekly cron (Typer CLI, asyncio.run())
   ├── httpx.AsyncClient → scrape every configured source
   ├── Instructor (async) → Ollama → structure events (text sources only)
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
uv run cine-event-bot prune-db        # delete ordinary screenings older than 14 days
uv run cine-event-bot run-bot         # start the Telegram bot
uv run cine-event-bot reset-db --yes  # drop + recreate all tables (wipes data)
```
