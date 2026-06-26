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

Structured JSONL logs are written to `logs/app.jsonl` (and stdout). Filter
errors without loading the whole file:

```fish
grep '"level":"ERROR"' logs/app.jsonl | tail
```

Failed LLM extractions and enrichments include the exception traceback in the
`exc` field.
