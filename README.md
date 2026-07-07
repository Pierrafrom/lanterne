# cine-event-bot

Self-hosted Telegram bot that aggregates **special screenings** in Paris/IDF —
avant-premières (with the film team), ciné-concerts, retrospectives,
open-air, festivals, cult/midnight screenings, ciné-clubs, short-film
programmes —
from eight scraped sources, enriches them with TMDB data, sends a **weekly
digest**, and answers **natural-language questions** about programmed films.

## Features

- **Eight sources**, each scraped at the most robust level (see
  [scraping strategy](docs/scraping-strategy.md)): Première Projo (structured
  Next.js JSON, no LLM), La Cinémathèque française, Le Forum des images,
  Le Champo, Le Louxor, the Fondation Jérôme Seydoux-Pathé, and La Villette's
  open-air cinema (HTML + LLM extraction), and Paris Ciné Info (authenticated
  JSON API covering dozens of Paris cinemas at once, LLM classification of
  each showtime's free-text comment only — see
  [ADR 0007](docs/decisions/0007-paris-cine-info.md); requires a personal
  account, optional).
- **Cross-source deduplication** — the same screening on two sources collapses
  to one, enriched from both.
- **TMDB enrichment** — synopsis, poster, director, genres, runtime,
  release year, and rating, fetched once per film and shared by all its
  screenings.
- **Weekly digest** in French, grouped by day, in Paris local time.
- **Natural-language Q&A** — questions are turned into structured filters by the
  LLM and run against the database (no RAG).
- **Async end to end** (aiogram, httpx, aiosqlite, Instructor/Ollama).

## Quickstart

```fish
git clone https://github.com/Pierrafrom/cine-event-bot
cd cine-event-bot
uv sync --all-groups
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN, TMDB_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL

uv run cine-event-bot scrape          # scrape + enrich + persist (idempotent)
uv run cine-event-bot stats           # summary of stored events
uv run cine-event-bot weekly-digest   # broadcast the week's digest
uv run cine-event-bot backup-db       # timestamped snapshot into ./backups/
uv run cine-event-bot run-bot         # start the Telegram bot
```

Full instructions (prerequisites, Ollama model, Docker, observability) in
[docs/setup.md](docs/setup.md).

## Test

```fish
uv run pytest
```

See [docs/testing.md](docs/testing.md) for conventions and the no-network
testing approach.

## Documentation

- [docs/architecture.md](docs/architecture.md) — components, async flow, diagrams
- [docs/setup.md](docs/setup.md) — install, configure, run, Docker
- [docs/testing.md](docs/testing.md) — how to run and write tests
- [docs/scraping-strategy.md](docs/scraping-strategy.md) — how to ingest a new source
- [docs/decisions/](docs/decisions/) — Architecture Decision Records
- [CONTRIBUTING.md](CONTRIBUTING.md) — local workflow, gates, branch/PR conventions
- [CHANGELOG.md](CHANGELOG.md) — notable changes
- [CLAUDE.md](CLAUDE.md) — project context for AI assistants
