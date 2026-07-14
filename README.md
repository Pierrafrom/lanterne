# cine-event-bot

Self-hosted Telegram bot that tracks cinema screenings in Paris/IDF, flags
**special screenings** — avant-premières (with the film team), ciné-concerts,
retrospectives, open-air, festivals, cult/midnight screenings, ciné-clubs,
short-film programmes — from seven scraped sources, enriches them with TMDB
data, sends a **weekly digest** of the specials, and answers
**natural-language questions** over every stored screening.

## Features

- **Seven active sources**, each scraped at the most robust level (see
  [scraping strategy](docs/scraping-strategy.md)): Première Projo
  (structured Next.js JSON, no LLM), La Cinémathèque française,
  Le Forum des images, the Fondation Jérôme Seydoux-Pathé, and La Villette's
  open-air cinema (HTML + LLM extraction), Paris Ciné Info (authenticated
  JSON API, the full Paris intra-muros catalogue — see
  [ADR 0007](docs/decisions/0007-paris-cine-info.md); requires a personal
  account, optional), and offi.fr (the Île-de-France suburb complement,
  tabular HTML, no LLM). MK2, Le Louxor, and Le Champo were retired once
  confirmed redundant with Paris Ciné Info's own coverage — see
  [ADR 0011](docs/decisions/0011-retire-mk2-louxor.md) and
  [ADR 0012](docs/decisions/0012-retire-lechampo.md).
- **Every screening is stored**, not only special ones — Paris Ciné Info and
  offi.fr are *width sources* covering every showtime at their venues; a
  rule-based classifier (`core/specialness.py`) upgrades an ordinary
  screening when team presence, a cycle name, an institution venue, or the
  film's age signals it — see [ADR 0008](docs/decisions/0008-drop-allocine-width-source.md)
  and [ADR 0009](docs/decisions/0009-specialness-rules-only.md). The weekly
  digest stays specials-only; the Q&A bot searches everything.
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
uv run cine-event-bot prune-db        # delete ordinary screenings older than 14 days
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
- [docs/coverage-matrix.md](docs/coverage-matrix.md) — venue coverage across sources, open follow-ups
- [docs/performance-audit.md](docs/performance-audit.md) — where pipeline time goes, bottlenecks, tooling evaluation
- [docs/decisions/](docs/decisions/) — Architecture Decision Records
- [CONTRIBUTING.md](CONTRIBUTING.md) — local workflow, gates, branch/PR conventions
- [CHANGELOG.md](CHANGELOG.md) — notable changes
- [CLAUDE.md](CLAUDE.md) — project context for AI assistants
