# 0014 — Migrate the database from SQLite to PostgreSQL

- Status: accepted
- Date: 2026-07-15

## Context

The database has been SQLite since the project's start: a single file,
`aiosqlite` driver, WAL mode enabled for concurrent scraper/bot access
(`io/db.py::_enable_wal`), backed up with `VACUUM INTO`. That has worked
well for a single-writer bot with one active process pair (scrape cron +
bot polling).

The stack is now expanding to host services SQLite is a poor fit for
(see [`docs/vision.md`](../vision.md)): a public API serving concurrent
read traffic, a website, eventually a mobile app — all reading the same
data a scrape run writes. SQLite's single-writer model and file-based
deployment story (no network access, no role-based permissions, awkward
to run as a "service" other containers depend on) do not scale to that
shape, even though today's write volume is still modest.

## Decision

Migrate to PostgreSQL 18, run as its own container in the Docker Compose
stack, not exposed publicly — only reachable by other services on the
internal Docker network (see
[0015](0015-uv-workspace-monorepo.md) for how the stack composes) and, for
local inspection, a dockerized DB client bound to `127.0.0.1` only.

This is a full migration, not a dual-support shim:

- The SQLite-only idioms in `io/db.py` (`PRAGMA journal_mode=WAL`,
  `VACUUM INTO` backups, the `StaticPool` in-memory branch) are removed,
  not kept behind a dialect branch — the codebase targets Postgres only
  from this point on.
- `sa_type=JSON` columns (`Film.genres`, `Venue.accepted_passes`,
  `ScreeningEvent.specialness_reasons`) move to `JSONB`, Postgres's native
  binary JSON type — indexable and queryable, unlike SQLite's
  text-backed `JSON`.
- `UtcDateTime` keeps its role (normalize to UTC on write, re-attach UTC
  on read) but now backs a real `timestamptz` column instead of working
  around SQLite's lack of timezone-aware storage.
- The Alembic migration chain is **not** carried forward from SQLite (see
  [0016](0016-tests-on-postgres.md) for the corresponding test-harness
  change) — a single clean baseline migration is generated against
  Postgres, since no production Postgres database exists yet to migrate
  incrementally. The historical SQLite migration chain stays in git
  history for reference.
- Backups switch from `VACUUM INTO` to `pg_dump` (custom format,
  restorable with `pg_restore`), scheduled the same way `backup-db`
  already is today. `pg_dump` is a system binary (`postgresql-client`
  package), not a Python dependency — required on `PATH` wherever
  `backup-db` runs (baked into the Docker image; a local dev machine
  needs it installed separately from `uv sync`).
- Every `StrEnum`-backed column (`EventType`, `Source`, `RatingSource`,
  `VenueKind`) explicitly uses
  `sa_type=Enum(..., native_enum=False, create_constraint=False)` instead
  of SQLModel's default for an Enum-typed field. Without this, SQLAlchemy
  creates a native PostgreSQL `ENUM` type by default — invisible on
  SQLite, where enums were always plain `TEXT` — which would turn adding
  a category (a new `EventType`, a new scraped `Source`) into a schema
  migration (`ALTER TYPE ... ADD VALUE`), regressing a property this
  project already relies on (see `EventType`'s docstring in
  `core/models.py`). `native_enum=False` keeps the column a plain
  `VARCHAR`; `create_constraint=False` avoids a `CHECK` constraint that
  would carry the same migration cost. SQLAlchemy's usual Python
  `Enum` ↔ database-value round-trip on read/write is unaffected by
  either flag.
- `SQLModel.metadata.naming_convention` is set explicitly (`ix_`, `uq_`,
  `ck_`, `fk_`, `pk_` prefixes) before any table is defined — without it,
  PostgreSQL and SQLAlchemy auto-generate constraint names that can
  differ run to run, making Alembic autogenerate diffs unreliable and a
  future hand-written `DROP CONSTRAINT` migration ambiguous.

## Data migration

The existing SQLite database (808 films, 41,231 screenings, 41,282
sightings, 291 venues, 2,443 ratings as of 2026-07-15) is migrated with a
dedicated, idempotent script
(`scripts/migrate_sqlite_to_postgres.py`, see
[`docs/setup.md`](../setup.md) for the runbook), not by hand and not as a
side effect of any application code path:

- Reads only from a **backup** SQLite file, never the live database.
- Inserts into Postgres in foreign-key-safe order (`Venue`, `Film` →
  `ScreeningEvent` → `EventSighting`/`FilmRating` → `Subscriber`),
  preserving primary keys so `dedup_key` and every foreign key stay
  intact, then resets Postgres sequences with `setval` to continue past
  the highest migrated id.
- Has a read-only `--check` mode that compares row counts (and a
  checksum per table) between the SQLite source and the Postgres target
  without writing anything — used both before the real run (dry run) and
  after (verification).
- Is safe to re-run: an already-migrated row is detected by primary key
  and skipped, not duplicated.

The old SQLite database file is **never deleted automatically** by any
script or CI step. Deletion only happens after the project owner
manually verifies the migrated data (row counts, spot checks) and
explicitly confirms — see the runbook for the exact sequence.

## Consequences

- Every environment (dev, CI, prod) needs a running Postgres — no more
  zero-dependency single-file database. Docker Compose (already the
  chosen deployment shape, see 0015) makes this a non-issue: `postgres`
  is one more service in the same stack.
- Tests run against a real Postgres container instead of SQLite
  in-memory — see [0016](0016-tests-on-postgres.md) for the full
  rationale and trade-off (Docker becomes a hard requirement to run the
  test suite, locally and in CI).
- Concurrent read access (API, future website) no longer contends with
  the scraper/bot writers — Postgres's MVCC replaces WAL as the
  concurrency story.
- `JSONB` and `timestamptz` are strictly more capable than their SQLite
  counterparts; no query or model logic needs to special-case the new
  types beyond the mechanical `sa_type=JSON` → `JSONB` swap.
