# 0017 — Dev Docker Compose stack: PostgreSQL, GPU-accelerated Ollama, profile-gated scraper

- Status: accepted
- Date: 2026-07-16

## Context

Before this decision, `docker-compose.yml` ran a single `bot` service with
no database or LLM of its own — `DATABASE_URL` and `OLLAMA_BASE_URL` had to
point at externally-reachable instances. With PostgreSQL now the database
(see [ADR 0014](0014-postgres-migration.md)) and Ollama a hard dependency
for extraction/Q&A, the dev stack needs to be self-contained: `docker compose up` should bring up a fully working environment with no manual
setup beyond `.env`.

The user explicitly asked for GPU-accelerated Ollama in the stack (an
NVIDIA GPU is available on the dev machine) and for no reverse proxy in
dev — each service reachable directly on its own port, Caddy/equivalent
reserved for `docker-compose.prod.yml` (not built yet).

## Decision

`docker-compose.yml` now has five services: `postgres`, `ollama`,
`ollama-pull` (one-shot), `bot` (long-running), and `scraper`
(profile-gated, one-shot). The `lanterne` image build moved to
`docker/lanterne.Dockerfile` (build context stays the repository root, for
the shared uv workspace lockfile — see
[ADR 0015](0015-uv-workspace-monorepo.md)).

Several non-obvious points, each verified against a real, running stack
rather than assumed:

- **`postgres:18`'s volume layout changed**: the official image now stores
  data under version-specific subdirectories of `/var/lib/postgresql`
  (`pg_ctlcluster`-compatible, see
  [docker-library/postgres#1259](https://github.com/docker-library/postgres/pull/1259)),
  not directly under `/var/lib/postgresql/data` as older images did.
  Mounting the volume at the old path makes the container refuse to start
  (`Error: ... these Docker images are configured to store database data in a format which is compatible with "pg_ctlcluster"`) — the compose
  file mounts `/var/lib/postgresql` instead.
- **Host ports default away from the standard ones**:
  `POSTGRES_HOST_PORT`/`OLLAMA_HOST_PORT` default to `5433`/`11435`, not
  PostgreSQL's `5432` or Ollama's `11434`. Verified live on the dev
  machine: both standard ports were already bound by an unrelated project's
  Postgres container and a natively-installed Ollama respectively —
  publishing on the standard ports would silently fail to bind or, worse,
  collide with a completely different project's database. Containers still
  reach each other on the standard ports via the compose-internal network
  (`postgres:5432`, `ollama:11434`); only the host-published side moved.
  Both are bound to `127.0.0.1`, never `0.0.0.0`.
- **`ollama` requests a GPU via Compose's native device-reservation
  syntax** (`deploy.resources.reservations.devices`, `driver: nvidia`,
  `capabilities: [gpu]`) — confirmed working end-to-end on this machine
  (`docker compose exec ollama nvidia-smi` lists the RTX 4070 inside the
  container) via the NVIDIA Container Toolkit, not the older
  `runtime: nvidia` style. A host without the toolkit needs the
  `deploy.resources.reservations.devices` block removed to run CPU-only.
- **`ollama-pull` is a genuine one-shot service**, not a background job:
  it runs `ollama pull ${OLLAMA_MODEL}` against the `ollama` service over
  the network (`OLLAMA_HOST=ollama:11434`) and exits — the model is
  downloaded server-side into `ollama`'s own named volume, so
  `ollama-pull` needs no volume of its own. Re-running it when the model
  is already present is a fast no-op (confirmed: pull skips already-cached
  layers).
- **`scraper` is `profiles: ["scraper"]`-gated**, so a plain
  `docker compose up` never starts it — scraping is a one-off/cron-
  triggered action, not a daemon. `docker compose run --rm scraper` (or
  with any other `lanterne` subcommand appended) runs it on demand. `bot`
  has no profile and starts with a plain `docker compose up bot`
  (`restart: unless-stopped`).
- **`bot`/`scraper` wait on `postgres`/`ollama` being `service_healthy`**,
  not just started — confirmed the whole chain end to end: container
  build → `migrate_to_head()` against the real `postgres` service →
  `lanterne stats`/`lanterne run-bot` reaching the point where only a
  syntactically-invalid dummy Telegram token stopped it (i.e. every layer
  below Telegram auth — DB, migrations, Ollama connectivity — works).

A genuine, pre-existing bug surfaced during this end-to-end testing (not
introduced by this decision, but only caught by actually running the full
stack rather than unit-testing pieces in isolation): `Settings.admin_chat_id`
(`int | None`) rejected a blank `ADMIN_CHAT_ID=` in `.env` — pydantic reads
an unquoted blank env value as `""`, which fails `int` validation instead
of falling back to the field's `None` default, contradicting
`.env.example`'s own "leave empty to disable" instruction. Fixed with a
`field_validator(mode="before")` on `Settings` that treats `""` as unset,
with a regression test (`test_settings_blank_admin_chat_id_is_treated_as_unset`).

## Consequences

- `docker compose up` (with the infra services and `ollama-pull` run
  first) is now a complete, self-contained dev environment — no external
  Postgres or Ollama instance required.
- Anyone whose host already runs Postgres or Ollama outside this project
  doesn't get a silent port conflict — the non-standard default host ports
  are deliberate, not arbitrary.
- GPU acceleration is opt-out (remove one block), not opt-in requiring the
  user to hand-write the device-reservation syntax themselves.
- The `Settings` blank-value fix generalizes: any future optional
  non-string field (another `int | None`) would hit the same failure mode
  unless it also normalizes `""` — worth remembering if one is added.
