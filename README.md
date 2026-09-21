# Lanterne

[![CI](https://github.com/Pierrafrom/lanterne/actions/workflows/ci.yml/badge.svg)](https://github.com/Pierrafrom/lanterne/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Checks](https://img.shields.io/badge/ruff%20%7C%20mypy%20--strict%20%7C%20pytest-passing-brightgreen)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-active%20development-orange)](#status)

A self-hosted Telegram bot that watches Paris/IDF cinema listings for me so I
don't have to check seven different websites to know when something worth
seeing is on.

## The problem

Paris has an unusually dense repertory-cinema scene — avant-premières with
the film team, ciné-concerts, festivals, retrospectives, open-air
screenings, ciné-clubs — but that program is scattered across a dozen venue
websites with no shared feed or alerting. Finding out that a director is
doing a Q&A three days from now means either remembering to check every
site or missing it. Lanterne exists to solve that for myself: it scrapes
every source once a week, decides on its own which screenings are actually
worth flagging, and pushes a digest to Telegram — plus lets me just ask it
things ("is there anything special at La Cinémathèque this week?") instead
of browsing.

## What it does

- **Seven scraped sources** — Première Projo, La Cinémathèque française, Le
  Forum des images, the Fondation Jérôme Seydoux-Pathé, La Villette's
  open-air cinema, Paris Ciné Info (the full Paris intra-muros catalogue,
  authenticated), and offi.fr (the Île-de-France suburb complement). Each
  is scraped at the most robust level available for that site — structured
  JSON/API where one exists, LLM extraction only where it doesn't. Detail
  and the reasoning behind every source added or dropped is in
  [docs/scraping-strategy.md](docs/scraping-strategy.md) and
  [docs/decisions/](docs/decisions/) (ADRs).
- **Every screening is stored, not just the special ones** — a rule-based
  classifier (`core/specialness.py`) decides after ingestion whether a
  showtime qualifies as special (team presence, a named cycle, an
  institutional venue, the film's age). The weekly digest only surfaces
  specials; a natural-language Q&A can search everything.
- **Cross-source deduplication** — the same screening reported by two
  sources collapses into one record, enriched from both.
- **TMDB enrichment** — synopsis, poster, director, genres, runtime,
  release year and ratings (IMDb, Allociné, SensCritique, Rotten Tomatoes,
  Metacritic, Letterboxd where available), fetched once per film and
  shared across every screening of it.
- **Weekly digest** in French, grouped by day, in Paris local time.
- **Natural-language Q&A over Telegram** — a question is turned into a
  structured database filter by an LLM (Instructor + a local Ollama model),
  not RAG.

## Status

Actively evolving, not a finished product. The database is mid-migration
from "special screenings only" to "every screening, specialness detected
after the fact" — see
[ADR 0008](docs/decisions/0008-drop-allocine-width-source.md) and
[docs/coverage-matrix.md](docs/coverage-matrix.md) for where that rollout
currently stands and which venues are and aren't covered yet. Expect the
scraper roster and the specialness rules to keep changing as coverage gaps
get found.

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+, `uv` for dependency management |
| Bot / async runtime | `aiogram`, `httpx`, `asyncio` end to end |
| Data | `SQLModel` (SQLAlchemy + Pydantic) on SQLite, `alembic` migrations |
| LLM extraction | `instructor` (structured output) over a local `Ollama` model |
| Scraping | `httpx` + `BeautifulSoup4`, JSON/API where available |
| CLI | `Typer` |
| Quality gate | `ruff`, `mypy --strict`, `pytest`, enforced in CI and pre-commit |

## Quickstart

```fish
git clone https://github.com/Pierrafrom/lanterne
cd lanterne
uv sync --all-groups
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN, TMDB_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL

uv run lanterne scrape          # scrape + enrich + persist (idempotent)
uv run lanterne stats           # summary of stored events
uv run lanterne weekly-digest   # broadcast the week's digest
uv run lanterne backup-db       # timestamped snapshot into ./backups/
uv run lanterne prune-db        # delete ordinary screenings older than 14 days
uv run lanterne run-bot         # start the Telegram bot
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
