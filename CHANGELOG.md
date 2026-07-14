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
- Every screening's digest/Q&A line now renders a clickable "Réserver" HTML
  link — a direct ticket-purchase link when the source provides one,
  otherwise falling back to the announcement page, so almost every screening
  now offers somewhere useful to book from.
- `Venue.accepted_passes`: which subscription cards a venue accepts (UGC
  Illimité, Pathé CinéPass, Cinémathèque LibrePass...), enriched from Paris
  Ciné Info's venue-passes catalogue at the end of every `scrape` run.
- Specialness classifier feedback loop: every classified screening's full
  feature vector and verdict (fired or not) is logged as structured JSONL,
  building a real labeled dataset from production runs to retune the rule
  thresholds against.
- Admin report enrichment: a database-shape section (venue-kind breakdown,
  specialization rate) after every `scrape` run, plus a volume-regression
  warning when a width source (Paris Ciné Info, offi.fr) returns suspiciously
  few events relative to its known catalogue size.
- `Venue.paris_cine_info_tid`: Paris Ciné Info's own stable theatre
  identifier per venue, carried on every `Sighting` it produces as
  `venue_external_id`. A stronger natural key than the text-based `slug`
  when available — the same role `Film.tmdb_id` already plays for films —
  used to recognize the same physical venue across sources that describe it
  with different wording.
- `Venue.address`/`website`/`seat_count`/`screen_width_m`/`screen_height_m`:
  room-level detail enriched from Paris Ciné Info's per-room
  `get_pcitheatre.php` endpoint. Seat count and screen dimensions are only
  filled in for a venue with exactly one distinct room seen in a run — this
  codebase models one `Venue` row per display name, not per physical room,
  so a multi-room venue (e.g. Le Louxor, MK2 Nation) has no single
  unambiguous seat count to store; address and website are cinema-level and
  always filled in regardless.
- `core/frenchdate.py::resolve_next_occurrence`: deterministic resolution of
  a year-less French day+month date against a reference date, and
  `structure_via_llm`'s new `known_starts_at` parameter to correct an LLM
  extraction's `starts_at` with it before plausibility validation runs —
  reused by `forumdesimages.py` and `lavillette.py` (see the Fixed section).

### Removed

- MK2 (`mk2.py`) and Le Louxor (`louxor.py`) bespoke scrapers, confirmed
  redundant with Paris Ciné Info's coverage after a corrected live
  spot-check (a first exact-dedup-key comparison wrongly found low overlap
  due to venue-name fragmentation and exact-timestamp mismatches — see
  [ADR 0011](docs/decisions/0011-retire-mk2-louxor.md)). `Source.MK2` and
  `Source.LE_LOUXOR` are kept for the historical `EventSighting` rows
  already recorded under them.
- Le Champo (`lechampo.py`) bespoke scraper: a live query confirmed Paris
  Ciné Info reports 89 current Le Champo showtimes directly, the same
  standard of evidence that justified retiring MK2/Le Louxor — retired
  rather than fixing a year-less-date resolution bug in a scraper no longer
  needed (see [ADR 0012](docs/decisions/0012-retire-lechampo.md)).
  `Source.LE_CHAMPO` is kept for its historical `EventSighting` rows.

### Changed

- MK2's 16 fragmented `Venue` rows (a scraper-name variant issue, see the
  Fixed section's `venue_external_id`/`merge_venue` entry) manually
  collapsed onto their 11 real physical rooms via
  `scripts/fix_mk2_venue_fragmentation.py`, using the live tid mapping
  confirmed against paris-cine.info — done immediately rather than waiting
  for the lazy self-heal to happen to touch each fragmented room on a future
  scrape. `docs/performance-audit.md` added: a real-run timing breakdown
  identifying Ollama running the extraction model on CPU (confirmed live —
  0% GPU utilization, 0 MiB VRAM used during inference) plus zero request
  parallelism as the dominant bottleneck (~half of a 61-minute run spent on
  78 LLM calls); both are infrastructure/config issues outside this
  session's reach, left as a follow-up. The three code-level findings were
  implemented: `IngestionPipeline.run()` now fetches every source
  concurrently (`asyncio.gather`) and ingests sequentially afterward, in
  registration order — same dedup semantics, total wall time now bounded by
  the slowest single source instead of their sum;
  `ParisCineInfoScraper.fetch_events`'s per-film showtimes discovery loop is
  now bounded-concurrency (`asyncio.Semaphore`, matching the pattern
  already used elsewhere in that file) instead of fully sequential; and the specialness
  classifier's N+1 query pattern (`EventRepository.count_distinct_venues`/
  `count_screenings`, one pair of queries per screening) is replaced by
  `venue_counts_by_film`/`screening_counts_by_film_and_venue`, two
  aggregate queries computed once per run regardless of screening count.
  Batching database commits (the audit's Finding 5) was evaluated and
  deliberately left out — it trades away `EventRepository`'s existing
  per-write fault isolation (see the `MissingGreenlet`/
  `PendingRollbackError` fixes above) for an estimated, not measured, gain.
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
- Alembic's `env.py` called `fileConfig()` with its default
  `disable_existing_loggers=True`, which silently disabled every
  `cine_event_bot.*` logger already created at import time as soon as the
  first migration ran — in effect killing all application logging (JSONL
  and console) for the rest of the process after the very first
  `Database.migrate_to_head()` call in nearly every CLI command.
- A film-enrichment failure (e.g. two differently-punctuated announced
  titles resolving to the same TMDB film and colliding on `Film.tmdb_id`'s
  unique constraint) triggers a session rollback, which expires every
  SQLAlchemy object the session had loaded; the failure handler used to log
  `film.title` straight from the now-expired object, crashing with
  `MissingGreenlet` on the implicit lazy-reload and taking down the entire
  `scrape` run instead of just skipping the one film. Found on the first
  real run at production volume (40k+ screenings).
- `ParisCineInfoScraper.fetch_venue_passes` re-authenticated on every call
  even when `fetch_events` had already logged the shared client in during
  the same run — confirmed live, a second login attempt on an
  already-authenticated session is rejected by paris-cine.info. The
  scraper now logs in at most once per instance.
- A film row whose enrichment discovered a TMDB match already owned by
  another `Film` row (two differently-worded announced titles for the same
  real film) used to lose its enrichment to a swallowed `IntegrityError` —
  one title pair alone caused 4,183 such collisions in the production run
  that surfaced this. `IngestionPipeline._enrich` now merges the duplicate
  into the row that already owns the TMDB id (repointing every screening,
  then deleting the duplicate) instead of leaving it unenriched.
- Venue-name fragmentation across sources describing the same physical venue
  differently — confirmed live as 16 `Venue` rows for MK2's 11 physical
  rooms, which is what made the first MK2/Le Louxor retirement spot-check
  ([ADR 0010](docs/decisions/0010-keep-mk2-champo-louxor.md)) wrongly look
  like a coverage gap (see [ADR 0011](docs/decisions/0011-retire-mk2-louxor.md)).
  `EventRepository._resolve_venue` now checks Paris Ciné Info's stable
  `venue_external_id` before falling back to the text-based `slug`, and
  self-heals a pre-existing fragmented row (stamp or `merge_venue`) the next
  time any of that venue's showtimes is (re-)ingested — no separate cleanup
  pass needed.
- `forumdesimages.py` and `lavillette.py` used the same year-less
  reference-date-resolution pattern that broke the now-retired
  `lechampo.py` (embed a reference date in the LLM prompt, ask the model to
  resolve "the next upcoming occurrence" itself). Both now resolve the date
  deterministically in Python (`core/frenchdate.py::resolve_next_occurrence`)
  and correct the LLM's output with it via `structure_via_llm`'s new
  `known_starts_at` parameter — the LLM still classifies
  `event_type`/`cycle_name`/`has_team_present`, but its own date arithmetic
  is never trusted, the same "known-true facts win" posture already proven
  in `paris_cine_info.py`.
