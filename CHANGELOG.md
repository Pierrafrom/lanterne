# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Async ingestion pipeline: scrape → structure → TMDB-enrich → deduplicate →
  persist (SQLite via aiosqlite), idempotent across re-runs.
- Nine source scrapers behind a `SourceScraper` protocol:
  - No LLM (structured JSON): Première Projo and MK2 (both embedded Next.js
    RSC JSON).
  - HTML + LLM extraction (Instructor/Ollama): La Cinémathèque française,
    Le Forum des images, Le Champo, Le Louxor, and the Fondation Jérôme
    Seydoux-Pathé.
  - Level 4, seasonal: La Villette's open-air cinema programme.
  - Hybrid Level 2 + 4, optional: Paris Ciné Info — an authenticated JSON API
    (personal account required) covering dozens of Paris cinemas at once,
    with the LLM only classifying each showtime's free-text comment. See
    [ADR 0007](docs/decisions/0007-paris-cine-info.md).
- Relational schema normalized around the screening: `Film` and `Venue`
  tables (enrichment shared across every screening instead of duplicated per
  row) plus `EventSighting` for full cross-source provenance (every source
  that reported a screening is now recorded, not just the first). See
  [ADR 0006](docs/decisions/0006-relational-schema-split.md).
- Alembic migrations, SQLite WAL mode, and a `backup-db` command
  (`VACUUM INTO` snapshot).
- Full TMDB enrichment per film: director, genres, runtime, and rating, in
  addition to synopsis/poster/release year — fetched once per film and
  shared by all its screenings.
- Four more event types (festival, séance culte, ciné-club, court métrage),
  with prompts and French digest/Q&A labels generated from the enum so a new
  category can't be added without also teaching them.
- Post-extraction plausibility guards (implausible dates, blank/oversized
  fields) reject bad LLM output before it reaches the database.
- `eval-extraction` command: scores the configured LLM's extraction quality
  against a committed golden dataset (per-field accuracy, exact-match rate,
  latency).
- Optional admin Telegram report after each `scrape` run (per-source event
  counts, zero-events warnings, failures) via `ADMIN_CHAT_ID`.
- Cross-source deduplication with a first-seen-wins, later-enriches merge.
- Telegram bot: `/start` and `/stop` digest subscriptions, a weekly French
  digest grouped by day in Paris time, and natural-language Q&A (LLM → SQL
  filter, no RAG).
- Admin CLI (`cine-event-bot`): `scrape`, `stats`, `weekly-digest`, `run-bot`,
  `backup-db`, `eval-extraction`, `reset-db`, with a live per-source progress
  display.
- Observability: JSONL file logs plus a human-readable Rich console.
- Documentation: architecture (with diagrams), setup, testing, scraping
  strategy, and Architecture Decision Records (0001-0007).

### Changed

- Scrapers report progress during extraction so the bar is determinate; per-item
  work runs with bounded concurrency; LLM extraction retries once before
  skipping a listing.
- Scrapers now produce `Sighting` objects (extracted facts + provenance)
  rather than building persisted rows directly; `EventRepository.ingest` is
  the single entry point resolving film/venue rows and the dedup key.

### Fixed

- Datetimes are stored and read back as timezone-aware UTC (SQLite otherwise
  drops the timezone).
- Le Louxor's event-index parser compared each raw `href` against a list
  already holding absolutized URLs, so deduplication never actually
  triggered.
- MK2 events spanning several linked cinemas (routinely 4-5 at once for a
  wide-release avant-première) only ever expose one `nextSession` — the
  other venues' screenings are now surfaced as a warning log instead of
  being silently dropped; capturing them fully needs the same client-side
  API discovery already deferred for UGC.
