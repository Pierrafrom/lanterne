# Setup

Full installation and configuration guide. For a one-minute overview see the
[README](../README.md); for the design see [architecture.md](architecture.md).

## Prerequisites

- **Python ≥ 3.11** (uses `StrEnum`, `zoneinfo`, `datetime.UTC`).
- **[uv](https://docs.astral.sh/uv/)** for dependency management.
- **An [Ollama](https://ollama.com) instance** (local or remote) for the
  text-source extraction and the Q&A. A small model works but limits extraction
  quality; a larger model improves it.
- **A Telegram bot token** from [@BotFather](https://t.me/BotFather).
- **A TMDB API key** (v3 auth) from
  [themoviedb.org](https://www.themoviedb.org/settings/api).

## Install

```fish
git clone https://github.com/Pierrafrom/cine-event-bot
cd cine-event-bot
uv sync --all-groups          # create the venv and install everything
uv run pre-commit install     # enable the commit-time lint/format/type gate
```

## Configure

```fish
cp .env.example .env
```

Then fill `.env` (never commit it — it is gitignored):

| Variable             | Purpose                                                              |
| -------------------- | -------------------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather                                            |
| `TMDB_API_KEY`       | TMDB v3 API key                                                      |
| `OLLAMA_BASE_URL`    | Ollama base URL (e.g. `http://localhost:11434`)                      |
| `OLLAMA_MODEL`       | Model name as listed by `ollama list` (e.g. `llama3.2:3b`)           |
| `DATABASE_URL`       | Async SQLite URL (default `sqlite+aiosqlite:///./cine_event_bot.db`) |
| `LOG_LEVEL`          | `DEBUG` / `INFO` / `WARNING` / `ERROR`                               |

Pull the model referenced by `OLLAMA_MODEL` if needed:

```fish
ollama pull llama3.2:3b
```

## Run

```fish
uv run python -m cine_event_bot scrape          # scrape + enrich + persist
uv run python -m cine_event_bot weekly-digest   # broadcast the week's digest
uv run python -m cine_event_bot run-bot         # start the Telegram bot
```

`run-bot` answers `/start` (subscribe), `/stop` (unsubscribe), and any other
message as a natural-language question. Schedule `scrape` and `weekly-digest`
with cron (or any scheduler) for unattended operation.

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

The store is a plain SQLite file (`cine_event_bot.db` by default). To browse it
inside VS Code:

1. Install the **SQLite Viewer** extension (`qwtel.sqlite-viewer`) — read-only,
   zero-config: click the `.db` file to open a table browser. For running
   queries, use **SQLite** (`alexcvzz.vscode-sqlite`) instead and run
   *"SQLite: Open Database"* from the command palette.
1. Open `cine_event_bot.db`; the `screeningevent` table holds the events and
   `subscriber` the digest opt-ins.

Useful queries:

```sql
SELECT count(*), source FROM screeningevent GROUP BY source;
SELECT title, venue, starts_at, has_team_present
FROM screeningevent ORDER BY starts_at LIMIT 20;
SELECT count(*) FROM screeningevent WHERE has_team_present = 1;  -- team-present previews
```

Outside VS Code, the `sqlite3` CLI works too: `sqlite3 cine_event_bot.db`.
