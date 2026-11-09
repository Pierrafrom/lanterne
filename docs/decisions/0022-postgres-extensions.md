# 0022 — PostgreSQL extensions: pg_trgm/unaccent, pg_stat_statements, PostGIS

- Status: accepted
- Date: 2026-07-17

## Context

A survey of PostgreSQL extensions that could concretely improve this
project's data quality, search, and observability — not a generic "what's
available" list, but grounded in actual gaps found in the running code:

- `EventRepository.search`'s `text_query` filter (the bot's Q&A feature,
  and the future public API's search endpoint per
  [`docs/vision.md`](../vision.md)) did a plain, unindexed
  `Film.title.ilike('%...%')`/`Film.overview.ilike('%...%')` — a
  sequential scan on every search, and no accent handling (a user typing
  "cafe" would not match "Café").
- No query-level performance observability existed at all, despite
  `docs/vision.md`'s explicit "metrics/dashboards" pillar of the target
  stack.
- `Venue.latitude`/`longitude` (added in
  [ADR 0021](0021-venue-screen-split-and-geocoding.md)) are raw floats —
  fine for storage, but the interactive map's future "cinemas near me" /
  radius queries would otherwise need hand-rolled haversine SQL with no
  spatial index.

Extensions considered but explicitly deferred, not adopted here:

- **pgvector** — no concrete use case today; the project's LLM usage is
  extraction (turning scraped text into structured fields), not semantic
  search or embeddings. Revisit if a "similar films" or semantic search
  feature is ever designed.
- **pg_cron** — the project has no scheduler at all yet for `lanterne scrape`/`prune-db` (not host cron, not systemd timer, not pg_cron) —
  relevant only once a scheduling mechanism is actually chosen.
- **pgaudit, pg_partman** — out of scope at this project's scale (no
  regulatory audit requirement, 41k screening rows, nowhere near
  partitioning territory).

## Decision

### pg_trgm + unaccent — accent- and case-insensitive indexed search

`CREATE EXTENSION pg_trgm; CREATE EXTENSION unaccent;` plus an
`immutable_unaccent(text)` wrapper function and two GIN trigram indexes
(`ix_film_title_trgm`, `ix_film_overview_trgm`). `EventRepository.search`
now filters via `func.immutable_unaccent(Film.title).ilike(...)` instead
of a bare `ilike`, so "cafe" now matches "Café" and the query is
index-backed.

**A real, reproducible Postgres bug had to be worked around first.**
`unaccent()` is `STABLE`, not `IMMUTABLE`, so a functional trigram index
needs an `IMMUTABLE` wrapper — the standard pattern everywhere online uses
the old-style function body: `CREATE FUNCTION f(text) RETURNS text AS $$ SELECT unaccent($1) $$ LANGUAGE sql IMMUTABLE`. Confirmed live, repeatedly,
in isolated scratch databases: `CREATE INDEX ... USING gin (immutable_unaccent(title) gin_trgm_ops)` built with that style function
fails with `function unaccent(text) does not exist`, even though:

- calling the same function directly (`SELECT immutable_unaccent('café')`) always works, with a literal or a column
  reference alike;
- a plain trigram index with no wrapper function (`title gin_trgm_ops`)
  always works, immediately after `CREATE EXTENSION pg_trgm`, same
  transaction;
- splitting extension+function creation and the `CREATE INDEX` into
  separate, independently committed transactions (even separate database
  connections) did **not** fix it — ruling out a transaction-boundary or
  catalog-cache theory.

The actual fix: use Postgres's newer SQL-standard function body syntax
(PG14+), which resolves `unaccent` immediately at `CREATE FUNCTION` time
instead of deferring it:

```sql
CREATE FUNCTION immutable_unaccent(input text)
RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
RETURN unaccent(input)
```

With this form, `CREATE INDEX` on the wrapped expression works
immediately, in the same migration, same transaction as the extensions.
The root cause appears to be specific to how Postgres validates/inlines
old-style (`AS $$ ... $$`) SQL function bodies when building an expression
index — the new-style body pre-resolves and stores the dependency instead
of re-parsing raw text on first real use.

### pg_stat_statements — query-level observability

`shared_preload_libraries=pg_stat_statements` on the `postgres` service's
`command:` (required at server start — `CREATE EXTENSION` alone raises
without it) plus `CREATE EXTENSION pg_stat_statements`. The test suite's
`testcontainers` `PostgresContainer` mirrors the same `command:` so
`CREATE EXTENSION pg_stat_statements` doesn't fail there either. Directly
serves `docs/vision.md`'s observability pillar and
`rules/devops/devops.md`'s monitoring guidance, at zero application code
cost.

### PostGIS — a real geography type for `Venue.location`

`Venue.latitude`/`longitude` (two raw floats) are replaced by a single
`location: Geography(POINT, 4326)` column (via `geoalchemy2` +
`shapely`), with a GiST index (`ix_venue_location`, created explicitly by
the migration rather than relied on implicitly — Alembic's `op.add_column`
doesn't reliably trigger geoalchemy2's own DDL-event-based automatic index
creation the way `metadata.create_all()` does). `latitude`/`longitude`
survive as read-only Python properties (`to_shape(self.location).y`/`.x`),
so every existing caller (`geocode-venues`, tests, pgweb) keeps working
unchanged — only `VenueRepository.save_geocoding` (which now constructs a
`WKTElement`) and `list_venues_missing_coordinates` (`WHERE location IS NULL`) needed to change.

Base image: `postgres:18` → `postgis/postgis:18-3.6` (dev
`docker-compose.yml`; the prod overlay only resets ports, so it inherits
this unchanged). Confirmed compatible with PG18 and with this project's
existing `/var/lib/postgresql` volume layout
([ADR 0017](0017-dev-docker-compose-stack.md)) — swapping the image onto
the existing data volume preserved all 41,231 screenings and 289 venues
exactly, verified via `SELECT count(*)` before and after.

`Venue` needed `model_config = ConfigDict(arbitrary_types_allowed=True)`
— `WKBElement`/`WKTElement` (geoalchemy2) have no Pydantic-native schema;
this is safe because `location` is never set from untrusted external
input, only via `VenueRepository.save_geocoding`.

### Incidental fixes made along the way

- **`docker/lanterne.Dockerfile`'s `pg_dump` was version-skewed**: Debian's
  default repo installs whichever PostgreSQL client major version ships
  with the base image's Debian release (17), which refuses to dump an
  18.x server (`pg_dump: error: aborting because of server version mismatch`) — confirmed live, this broke `lanterne backup-db` entirely,
  pre-existing and unrelated to this ADR's own changes but blocking the
  "backup before migrating" step. Fixed by installing
  `postgresql-client-18` from the official PGDG apt repository (the
  documented `postgresql-common` + `apt.postgresql.org.sh` automated setup
  script), matching the server's actual major version instead of
  whatever Debian's own repo happens to carry.
- `tests/conftest.py`'s per-test `TRUNCATE TABLE` list was missing
  `screen` (added in [ADR 0021](0021-venue-screen-split-and-geocoding.md),
  never added to this list) — a latent test-isolation gap, fixed while in
  the area.
- `numpy` (a transitive dependency of `shapely`, itself pulled in for
  geoalchemy2) ships stubs using Python 3.12+ syntax (the `type X = ...`
  statement) that mypy can't parse under this project's `python_version = "3.11"` target (matching the actual `python:3.11-slim` runtime — not
  bumped just to silence this). Pinned `numpy<2.1` to a version whose
  stubs predate that syntax, rather than adding a broader mypy exclusion.

## Consequences

- Three extensions to keep in mind when reasoning about the schema:
  `pg_trgm`/`unaccent` (search), `pg_stat_statements` (observability),
  `postgis` (geography). None require application-level maintenance
  beyond what's already in the migrations.
- `immutable_unaccent`'s SQL-standard-body requirement is now the
  documented pattern for any *future* immutable wrapper around a
  non-immutable extension function used in an expression index — the
  old-style `AS $$ ... $$` form is not safe for that specific case in this
  codebase, confirmed empirically, not just by community folklore.
- The base Postgres image is now PostGIS-flavored in both dev and prod —
  a heavier image than plain `postgres:18`, accepted because the
  interactive map (the reason this was adopted) is an explicit,
  already-in-progress direction (ADR 0021's geocoding work fed directly
  into this).
- `Venue.latitude`/`longitude` remain readable exactly as before (Python
  properties), but are no longer independently settable — any code
  constructing a `Venue` with raw lat/lon floats must go through
  `location` (a `WKTElement`) instead. No current caller did this outside
  `VenueRepository.save_geocoding`, which was updated.
- pgvector/pg_cron/pgaudit/pg_partman are explicitly not adopted — revisit
  each only when a concrete feature actually needs it (semantic search,
  a chosen scheduler, a compliance requirement, or genuine data volume,
  respectively), not preemptively.
