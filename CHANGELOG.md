# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Async ingestion pipeline: scrape → structure → TMDB-enrich → deduplicate →
  persist (SQLite via aiosqlite), idempotent across re-runs.
- Three source scrapers behind a `SourceScraper` protocol: Première Projo
  (embedded Next.js RSC JSON, no LLM), La Cinémathèque française and Le Forum
  des images (HTML + LLM extraction via Instructor/Ollama).
- Cross-source deduplication with a first-seen-wins, later-enriches merge.
- TMDB enrichment (synopsis, poster, release year) per film.
- Telegram bot: `/start` and `/stop` digest subscriptions, a weekly French
  digest grouped by day in Paris time, and natural-language Q&A (LLM → SQL
  filter, no RAG).
- Admin CLI (`cine-event-bot`): `scrape`, `stats`, `weekly-digest`, `run-bot`,
  `reset-db`, with a live per-source progress display.
- Observability: JSONL file logs plus a human-readable Rich console.
- Documentation: architecture (with diagrams), setup, testing, scraping
  strategy, and Architecture Decision Records.

### Changed

- Scrapers report progress during extraction so the bar is determinate; per-item
  work runs with bounded concurrency; LLM extraction retries once before
  skipping a listing.

### Fixed

- Datetimes are stored and read back as timezone-aware UTC (SQLite otherwise
  drops the timezone).
