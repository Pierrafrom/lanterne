# Setup

Full installation and configuration guide. For a one-minute overview see the
[README](../README.md); for the design see [architecture.md](architecture.md).

## Prerequisites

- **Python ≥ 3.11** (uses `StrEnum`, `zoneinfo`, `datetime.UTC`).
- **[uv](https://docs.astral.sh/uv/)** for dependency management.
- **An [Ollama](https://ollama.com) instance** (local or remote) for the
  text-source extraction and the Q&A — see *Choosing a model* below.
- **A Telegram bot token** from [@BotFather](https://t.me/BotFather).
- **A TMDB API key** (v3 auth) from
  [themoviedb.org](https://www.themoviedb.org/settings/api).

## Install

```fish
git clone https://github.com/Pierrafrom/lanterne
cd lanterne
uv sync --all-groups          # create the venv and install everything
uv run pre-commit install     # enable the commit-time lint/format/type gate
```

## Configure

```fish
cp .env.example .env
```

Then fill `.env` (never commit it — it is gitignored):

| Variable                   | Purpose                                                        |
| -------------------------- | -------------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`       | Bot token from @BotFather                                      |
| `TMDB_API_KEY`             | TMDB v3 API key                                                |
| `OLLAMA_BASE_URL`          | Ollama base URL (e.g. `http://localhost:11434`)                |
| `OLLAMA_MODEL`             | Model name as listed by `ollama list` (e.g. `llama3.2:3b`)     |
| `DATABASE_URL`             | Async SQLite URL (default `sqlite+aiosqlite:///./lanterne.db`) |
| `LOG_LEVEL`                | `DEBUG` / `INFO` / `WARNING` / `ERROR`                         |
| `ADMIN_CHAT_ID`            | Optional — Telegram chat that receives the post-scrape report  |
| `PARIS_CINE_INFO_LOGIN`    | Optional — email of a personal paris-cine.info account         |
| `PARIS_CINE_INFO_PASSWORD` | Optional — its password                                        |

`PARIS_CINE_INFO_LOGIN`/`PARIS_CINE_INFO_PASSWORD` enable the Paris Ciné Info
source (see [ADR 0007](decisions/0007-paris-cine-info.md)) — an aggregator
covering dozens of Paris cinemas at once, requiring a personal account. Leave
both empty to skip it entirely; every other source works without it.

Pull the model referenced by `OLLAMA_MODEL` if needed:

```fish
ollama pull qwen2.5:7b
```

### Choosing a model

The text sources (Cinémathèque, Forum des images) call the LLM once per
screening, and Paris Ciné Info once per commented showtime; Première Projo is
mapped directly with no LLM. The model must follow a strict JSON schema (enum
`event_type`, boolean `has_team_present`).

- **Recommended local: `qwen2.5:7b`** (or `qwen2.5:3b` for speed). Qwen2.5 is the
  strongest small model for structured/JSON output and respects the enum far
  better than `mistral` or `llama3.2:3b`, which tend to emit free text like
  `"Not determined"` and fail validation.
- **Fastest option: a free cloud API.** On CPU, a 7B model can take ~30-60 s per
  screening. [Groq](https://groq.com)'s free tier runs Llama 3.3 70B at hundreds
  of tokens/second with reliable JSON (14 400 requests/day, no card) — point
  `OLLAMA_BASE_URL` at an OpenAI-compatible endpoint and set the model
  accordingly. Google Gemini Flash (1 500 req/day) is another option.

Robustness already built in: extraction retries only once on a validation error
(a weak model rarely self-corrects, and retries multiply slow calls), failed
screenings are skipped, and per-source extraction runs with bounded concurrency.

## Run

`uv sync` installs a `lanterne` console entry point, so commands are:

```fish
uv run lanterne scrape           # scrape + enrich + persist (idempotent)
uv run lanterne stats            # summary of stored events
uv run lanterne weekly-digest    # broadcast the week's digest
uv run lanterne run-bot          # start the Telegram bot
uv run lanterne backup-db        # timestamped snapshot into ./backups/
uv run lanterne prune-db         # delete ordinary screenings older than 14 days
uv run lanterne eval-extraction  # score the LLM on the golden dataset
uv run lanterne eval-specialness # score the specialness classifier
uv run lanterne reset-db --yes   # drop + recreate all tables (wipes data)
uv run lanterne --help           # list every command
```

(`uv run python -m lanterne <command>` still works identically.)

**Re-running is safe**: `scrape` upserts on a deduplication key, so running it
repeatedly never creates duplicates — it refreshes and enriches in place (see
[ADR 0002](decisions/0002-dedup-merge-strategy.md)). Use `reset-db` only when you
want a truly empty database.

`run-bot` answers `/start` (subscribe), `/stop` (unsubscribe), and any other
message as a natural-language question. Schedule `scrape`, `weekly-digest`,
and `prune-db` with cron (or any scheduler) for unattended operation —
`prune-db` deletes ordinary (`is_special=False`) screenings that started more
than 14 days ago, keeping the database bounded now that the width sources
(Paris Ciné Info, offi.fr — see [ADR 0008](decisions/0008-drop-allocine-width-source.md))
report every screening, not only special ones. Special screenings are never
pruned, regardless of age. Run it right after `scrape` in the same weekly
job, as its own step — not auto-chained inside `scrape` — same posture as
`backup-db`.

## Database schema, migrations, and backups

Every CLI command creates or upgrades the schema automatically by running the
**Alembic** migrations (`migrations/`) before touching the database — a fresh
checkout needs no manual step. Run the CLI from the repository root: the
migration configuration is read from `alembic.ini` and `pyproject.toml` there.

When you change a model in `core/models.py`, generate the migration alongside
it (never edit the schema by hand):

```fish
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head   # or just run any CLI command
```

File-backed databases run in SQLite **WAL mode**, so the polling bot and the
scrape cron can work concurrently. `backup-db` snapshots the live database with
`VACUUM INTO` (consistent even while the bot runs) into a timestamped file —
schedule it next to the weekly scrape.

## Docker

```fish
docker compose up
```

The compose stack runs the bot; point `OLLAMA_BASE_URL` at a reachable Ollama
instance and provide the `.env` file.

## Observability

Logs go to **two sinks**:

- **Console** — human-readable, colored, with the structured context shown as
  `key=value`. `scrape` additionally renders a live **progress bar per source**.
  Set `LOG_LEVEL=DEBUG` for more detail.
- **`logs/app.jsonl`** — one JSON object per line, for cheap machine/AI debugging:

```fish
grep '"level":"ERROR"' logs/app.jsonl | tail        # only errors
jq 'select(.msg=="question answered")' logs/app.jsonl  # what the Q&A LLM produced
```

Failed LLM extractions and enrichments include the exception traceback in the
`exc` field. Every answered question logs the interpreted `criteria` and the
number of `results`, so an empty answer is easy to diagnose (e.g. the model
resolving a relative date to the wrong window).

## Inspecting the database

The store is a plain SQLite file (`lanterne.db` by default). To browse it
inside VS Code:

1. Install the **SQLite Viewer** extension (`qwtel.sqlite-viewer`) — read-only,
   zero-config: click the `.db` file to open a table browser. For running
   queries, use **SQLite** (`alexcvzz.vscode-sqlite`) instead and run
   *"SQLite: Open Database"* from the command palette.
1. Open `lanterne.db`; `screeningevent` holds the screenings (joined to
   `film` and `venue`), `eventsighting` the per-source provenance, and
   `subscriber` the digest opt-ins — see
   [ADR 0006](decisions/0006-relational-schema-split.md) for the full schema.

Useful queries:

```sql
SELECT s.source, count(*) FROM eventsighting s GROUP BY s.source;
SELECT f.title, v.name AS venue, e.starts_at, e.has_team_present
FROM screeningevent e
JOIN film f ON f.id = e.film_id
JOIN venue v ON v.id = e.venue_id
ORDER BY e.starts_at LIMIT 20;
SELECT count(*) FROM screeningevent WHERE has_team_present = 1;  -- team-present previews
```

Outside VS Code, the `sqlite3` CLI works too: `sqlite3 lanterne.db`.
