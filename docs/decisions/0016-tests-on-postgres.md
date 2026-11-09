# 0016 — Run the test suite against a real Postgres container, not SQLite in-memory

- Status: accepted
- Date: 2026-07-15

## Context

The test suite (`tests/conftest.py`) currently builds a fresh
`sqlite+aiosqlite:///:memory:` database per test via
`Database.create_tables()`, bypassing Alembic entirely. This is fast and
dependency-free, but after [0014](0014-postgres-migration.md) it tests
against a database engine the application no longer runs on in
production. Two risks follow directly from that gap:

- `JSONB`, `timestamptz`, and any Postgres-specific constraint/index
  behavior are never exercised by the suite — a migration or a query
  could be valid SQLite and invalid (or silently different) Postgres.
- `Database.migrate_to_head()` (the actual production schema path) stays
  untested; only the SQLite-only `create_tables()` fast path is.

## Decision

Rewire `tests/conftest.py`'s `database` fixture to run against a real,
ephemeral Postgres instance via `testcontainers` (`testcontainers[postgres]`,
added as a dev dependency at the workspace root): one Postgres container
started per test session, a fresh schema per test (created through
`Database.migrate_to_head()`, exercising the real Alembic path — not
`create_tables()`, which is retired along with the SQLite-only code it
existed to fast-path around).

This is a full replacement, not an added option: no SQLite test path is
kept "just in case." Consistent with the project's "propreté finale"
priority for this migration (favor a clean, single-database-story result
over a lighter-touch dual-dialect compromise).

## Consequences

- **Docker becomes a hard requirement to run `pytest`**, locally and in
  CI — `testcontainers` needs a Docker daemon to start the Postgres
  container. This is not a new project dependency in practice: Docker is
  already required for this project's own deployment (Dockerfile,
  docker-compose.yml) and CI already runs on GitHub Actions runners with
  Docker available by default.
- Test runtime increases (container startup, real network round-trips
  per query instead of an in-process SQLite file) — mitigated by
  starting one container per test *session* rather than per test, and
  wrapping each test in a transaction rolled back afterward rather than
  recreating the schema every time, once that optimization is needed.
- The suite now genuinely exercises `migrate_to_head()`, `JSONB` columns,
  and `timestamptz` semantics — a schema change that works in tests is a
  much stronger signal that it works in production.
- CI workflow (`.github/workflows/ci.yml`) needs no additional service
  container definition: `testcontainers` starts and tears down its own
  Postgres container per run, it does not rely on a `services:` block.
