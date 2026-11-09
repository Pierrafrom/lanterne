# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Renamed the project from `cine-event-bot` to **Lanterne** (Python package,
  CLI entry point, database file, GitHub repository) ahead of upcoming
  public-facing surfaces (a public website, a public API, and X/Instagram
  auto-posting). No behavior change.
- Split `io/repository.py`'s 674-line `EventRepository` (ingestion/dedup,
  film management, venue management, and querying all mixed in one class)
  into a `io/repository/` package: `EventRepository` now composes a new
  `FilmRepository` and `VenueRepository`, each owning one concern. Public
  API unchanged — every caller still constructs a single
  `EventRepository(session)`. No behavior change.
- Split `io/scrapers/paris_cine_info.py`'s stateless parsing helpers (film
  ratings, venue passes, venue detail, small coercion helpers) into a new
  `paris_cine_info_parsing.py` — the remainder (scraper class plus its
  tightly-coupled showtime/LLM-classification flow) is one cohesive unit
  left as-is. No behavior change.
- Restructured the repository into a uv workspace: the application moved
  from `src/lanterne` to `packages/lanterne` (import path unchanged),
  making room for the additional services described in
  [`docs/vision.md`](docs/vision.md) (public API, website, mobile,
  observability — not implemented yet). See
  [ADR 0015](docs/decisions/0015-uv-workspace-monorepo.md).
- Migrated the database from SQLite to PostgreSQL: `asyncpg` replaces
  `aiosqlite`, `JSONB` replaces SQLite's `JSON` columns, every `StrEnum`
  column stores plain text (`native_enum=False`) rather than a native
  Postgres `ENUM` so adding a category still needs no migration, and
  `backup-db` now shells out to `pg_dump` instead of `VACUUM INTO`. The
  Alembic migration chain was reset to a single clean PostgreSQL baseline
  — no production Postgres data existed yet to migrate incrementally. See
  [ADR 0014](docs/decisions/0014-postgres-migration.md).
- The test suite now runs against a real, ephemeral PostgreSQL container
  (`testcontainers`) instead of SQLite in-memory, exercising the actual
  Alembic migration path rather than a metadata-only fast path. Docker is
  now required to run `pytest`, locally and in CI. See
  [ADR 0016](docs/decisions/0016-tests-on-postgres.md).
- `docker-compose.yml` is now a self-contained dev stack: PostgreSQL 18,
  GPU-accelerated Ollama (verified end to end against an NVIDIA GPU), a
  one-shot `ollama-pull` service, a long-running `bot` service, and a
  profile-gated one-shot `scraper` service — no external database or LLM
  instance required to bring up the whole chain. The `lanterne` image
  build moved to `docker/lanterne.Dockerfile`. Fixed a real bug surfaced
  by end-to-end testing: a blank `ADMIN_CHAT_ID=` in `.env` crashed
  `Settings()` instead of being treated as unset, contradicting
  `.env.example`'s own documented behavior. See
  [ADR 0017](docs/decisions/0017-dev-docker-compose-stack.md).
- Added `scripts/migrate_sqlite_to_postgres.py`: an idempotent, checked
  migration of the pre-Postgres SQLite database into PostgreSQL (read-only
  on the SQLite side, `--check` mode verifies row counts and a per-row
  content hash between source and target). Run once against this
  project's own SQLite data (808 films, 41,231 screenings, 41,282
  sightings, 291 venues, 2,443 ratings) — verified clean by both the
  script's own `--check` and the application reading the migrated data
  back through `lanterne stats`. See
  [ADR 0014](docs/decisions/0014-postgres-migration.md)'s "Data
  migration" section for the design.
- Added `pgweb` to `docker-compose.yml` as the dockerized PostgreSQL GUI
  client (read-only by default, `127.0.0.1`-only, port `8082`) — not
  Outerbase Studio as originally planned: investigated and built end to
  end (a custom image from Outerbase's own source, pinned to a specific
  commit, since no official Docker image exists), but its web build
  explicitly blocks Postgres connections (confirmed by inspecting its own
  client bundle, not just its docs). See
  [ADR 0018](docs/decisions/0018-postgres-gui-client-pgweb.md). Also
  pinned `ollama/ollama` to `0.32.0` (was `:latest`) while touching
  `docker-compose.yml`.
- Added `services/api` (FastAPI) and `services/web` (Next.js) as
  hello-world scaffolding, proving the deployment chain works end to end
  — `GET /health` on the API pings PostgreSQL via `lanterne.io.db.Database`;
  the web app's homepage renders that status via a server-side fetch.
  Both dockerized and wired into `docker-compose.yml`; CI gained a
  `web-quality` job (biome, `tsc`, `next build`) alongside the existing
  Python one. `services/mobile` and `services/observability` got
  placeholder READMEs. No business endpoints or pages — see
  [ADR 0019](docs/decisions/0019-hello-world-api-web.md).
- Added `docker-compose.prod.yml`, a Compose overlay (Caddy reverse proxy,
  automatic HTTPS, path-routes `/api/*` to `api` and everything else to
  `web`) applied on top of `docker-compose.yml`, never standalone. Clears
  every other service's dev-only host port with the `!reset []` merge
  tag — Compose concatenates list fields like `ports` across `-f` files by
  default, so a naive override would have left them exposed. The
  routing/TLS logic was verified against real running containers
  (`CADDY_DOMAIN=localhost`); a real deployment (public domain, actual
  Let's Encrypt issuance) was not, since no prod server exists yet. See
  [ADR 0020](docs/decisions/0020-prod-compose-overlay.md).

### Added

- Official CNC venue referential, phase 1 (see
  [ADR 0026](docs/decisions/0026-official-reference-data-layer.md)): a new
  `io/reference/` layer loads the CNC's active-establishments census and
  its Île-de-France complement, matches each against stored venues, and
  enriches a matched `Venue` with `cnc_id`, `insee_code`, `screen_count`,
  `seat_count`, `art_et_essai*`, `is_multiplex`, and `is_active` — never
  overwriting an already-known field, never creating a new `Venue` from
  official data alone. A match is only ever applied automatically when
  name, département, and geocoded distance all agree; everything else is
  logged for manual review via the new `sync-reference` CLI command
  (`--dry-run` available).
- Async ingestion pipeline: scrape → structure → TMDB-enrich → deduplicate →
  persist (SQLite via aiosqlite), idempotent across re-runs.
- Nine source scrapers behind a `SourceScraper` protocol (MK2, Le Louxor,
  and Le Champo were later retired in favor of confirmed Paris Ciné Info
  coverage — see [ADR 0011](docs/decisions/0011-retire-mk2-louxor.md) and
  [ADR 0012](docs/decisions/0012-retire-lechampo.md); seven sources remain
  active):
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
- Every `Venue` row missing an address (5 rows, confirmed live 2026-07-20)
  turned out to be a fragment of an already fully-enriched row, not a
  genuinely address-less venue: a stale pre-`known_venue`-fix Cinémathèque
  scrape, and four Paris Ciné Info showtimes whose `title` field carries an
  in-house ciné-club/bar name instead of the physical cinema (confirmed
  live: no `tid` is present for these, so `VenueRepository.resolve` can't
  match them by external id — see
  `test_missing_tid_leaves_venue_external_id_none`). The same audit found
  two more fragments by exact address/coordinate collision (`Luxy`/`Le Luxy`, `Écoles Cinéma Club`/`Ecoles Cinema Club`) and one venue carrying
  its sister venue's address outright (`Cinoches Desnos` had `Cinoches RN7`'s address — Grand Paris Sud operates both under one brand but they
  are two separate buildings ~1km apart). All eight fragments merged and
  both addresses corrected by
  `scripts/fix_venue_missing_address_fragmentation.py`;
  `core/dedup.py`'s `_VENUE_ALIASES` gained six new entries so the
  single-venue cases can't refragment — "Lost in Frenchlation", which
  rotates across at least five different Paris cinemas, was deliberately
  left unaliased instead of hardcoded to the one venue this fix observed.
- 7 of the 8 `needs_review` (`NAME_SEARCH`-geocoded) venues' addresses were
  wrong, in several cases in the wrong department entirely (confirmed live
  2026-07-21: "L'Étoile" guessed at Paris's Place Charles de Gaulle is
  actually in La Courneuve; "Rex" guessed inside Disneyland Paris is
  actually in Châtenay-Malabry) — each is `OFFI`-sourced, and each venue's
  own offi.fr page carries a real, addressed `schema.org/PostalAddress`
  block the scraper doesn't parse today. Corrected via
  `scripts/fix_needs_review_venue_addresses.py`. A full walk of offi.fr's
  7-department venue directory (166 listed venues) also found 4 real
  cinemas with no `Venue` row at all, plus one ("Les Yeux d'Elsa",
  Saint-Cyr-l'École) confirmed real but currently absent from offi.fr's own
  directory (likely dropped during a closure) — all 5 added as bare rows
  (no scraper coverage yet) via `scripts/add_missing_idf_venues.py`. See
  `docs/coverage-matrix.md`'s 2026-07-21 entry for the full audit and its
  one open caveat (offi.fr's directory is not a guaranteed-exhaustive IDF
  census).
- The 8th `needs_review` venue ("La Grange") turned out to be a real
  architecture gap, not a one-off bad address: the CNC registry confirms
  **three** distinct "La Grange" cinemas in Île-de-France (Roissy-en-Brie,
  Vaux-le-Pénil, and Bois-d'Arcy — officially "La Grange de la Tremblaye",
  which avoids the collision on its own), and offi.fr's own venue pages
  render at least two of them under the exact same plain text "La Grange".
  Unlike Paris Ciné Info, `offi.py` carried no per-venue external id, so
  `VenueRepository.resolve`'s name-based `slug` matching silently conflated
  whichever of them reported a showtime into one `Venue` row (id 139) —
  with a real risk of one venue's screening being dropped as a false
  duplicate of the other's at a coincidentally identical time. Fixed
  durably: every `offi.py` sighting now carries the venue page's own URL
  slug as `venue_external_id` (new `Venue.offi_slug` column, migration
  `f98fb56722e4`), and `resolve` gained a `source` parameter so it knows
  which of `Venue`'s per-source external-id columns to check — a name
  match whose already-claimed external id *differs* from the incoming one
  is no longer trusted, and a new, disambiguated row is created instead
  (see `test_ingest_does_not_conflate_two_homonym_venues`). The three real
  "La Grange" cinemas were added as separate rows, two pre-stamped with
  their known `offi_slug` so the next `scrape` run routes to them directly,
  via `scripts/add_disambiguated_la_grange_venues.py`; the old conflated
  row (id 139) is left alone deliberately — it only holds ordinary
  width-source screenings, which age out on their own via `prune-db`'s
  14-day retention.
