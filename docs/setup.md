# Setup

Full installation and configuration guide. For a one-minute overview see the
[README](../README.md); for the design see [architecture.md](architecture.md).

## Prerequisites

- **Python ≥ 3.11** (uses `StrEnum`, `zoneinfo`, `datetime.UTC`).
- **[uv](https://docs.astral.sh/uv/)** for dependency management.
- **A PostgreSQL 18 server** (local install, a container, or a managed
  instance) — see [ADR 0014](decisions/0014-postgres-migration.md). The
  `postgresql-client` system package (for `pg_dump`, used by `backup-db`)
  is a separate install from the Python driver (`asyncpg`, pulled in by
  `uv sync`) — e.g. `sudo apt install postgresql-client` on Debian/Ubuntu.
- **An [Ollama](https://ollama.com) instance** (local, remote, or the
  `docker-compose.yml` service) for the text-source extraction and the Q&A —
  see *Choosing a model* below. Running it via Docker Compose with GPU
  acceleration needs the
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  installed on the host; without it, drop the `deploy.resources.reservations.devices`
  block in `docker-compose.yml` to run CPU-only (slower extraction).
- **A Telegram bot token** from [@BotFather](https://t.me/BotFather).
- **A TMDB API key** (v3 auth) from
  [themoviedb.org](https://www.themoviedb.org/settings/api).

## Install

```fish
git clone https://github.com/Pierrafrom/lanterne
cd lanterne
uv sync --all-packages --all-groups          # create the venv and install everything
uv run pre-commit install     # enable the commit-time lint/format/type gate
```

## Configure

```fish
cp .env.example .env
```

Then fill `.env` (never commit it — it is gitignored):

| Variable                   | Purpose                                                       |
| -------------------------- | ------------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`       | Bot token from @BotFather                                     |
| `TMDB_API_KEY`             | TMDB v3 API key                                               |
| `OLLAMA_BASE_URL`          | Ollama base URL (e.g. `http://localhost:11434`)               |
| `OLLAMA_MODEL`             | Model name as listed by `ollama list` (e.g. `llama3.2:3b`)    |
| `DATABASE_URL`             | Async PostgreSQL URL (default points at `localhost:5432`)     |
| `LOG_LEVEL`                | `DEBUG` / `INFO` / `WARNING` / `ERROR`                        |
| `ADMIN_CHAT_ID`            | Optional — Telegram chat that receives the post-scrape report |
| `PARIS_CINE_INFO_LOGIN`    | Optional — email of a personal paris-cine.info account        |
| `PARIS_CINE_INFO_PASSWORD` | Optional — its password                                       |

`PARIS_CINE_INFO_LOGIN`/`PARIS_CINE_INFO_PASSWORD` enable the Paris Ciné Info
source (see [ADR 0007](decisions/0007-paris-cine-info.md)) — an aggregator
covering dozens of Paris cinemas at once, requiring a personal account. Leave
both empty to skip it entirely; every other source works without it.

Pull the model referenced by `OLLAMA_MODEL` if needed:

```fish
ollama pull qwen2.5:7b
```

### Choosing a model

The text sources (Cinémathèque, Forum des images) call the LLM once per
screening, and Paris Ciné Info once per commented showtime; Première Projo is
mapped directly with no LLM. The model must follow a strict JSON schema (enum
`event_type`, boolean `has_team_present`).

- **Recommended local: `qwen2.5:7b`** (or `qwen2.5:3b` for speed). Qwen2.5 is the
  strongest small model for structured/JSON output and respects the enum far
  better than `mistral` or `llama3.2:3b`, which tend to emit free text like
  `"Not determined"` and fail validation.
- **Fastest option: a free cloud API.** On CPU, a 7B model can take ~30-60 s per
  screening. [Groq](https://groq.com)'s free tier runs Llama 3.3 70B at hundreds
  of tokens/second with reliable JSON (14 400 requests/day, no card) — point
  `OLLAMA_BASE_URL` at an OpenAI-compatible endpoint and set the model
  accordingly. Google Gemini Flash (1 500 req/day) is another option.

Robustness already built in: extraction retries only once on a validation error
(a weak model rarely self-corrects, and retries multiply slow calls), failed
screenings are skipped, and per-source extraction runs with bounded concurrency.

## Run

`uv sync` installs a `lanterne` console entry point, so commands are:

```fish
uv run lanterne scrape           # scrape + enrich + persist (idempotent)
uv run lanterne stats            # summary of stored events
uv run lanterne weekly-digest    # broadcast the week's digest
uv run lanterne run-bot          # start the Telegram bot
uv run lanterne backup-db        # timestamped snapshot into ./backups/
uv run lanterne prune-db         # delete ordinary screenings older than 14 days
uv run lanterne geocode-venues   # resolve GPS coordinates for venues missing them
uv run lanterne backfill-ratings # one-off: backdrop/IMDb id/ratings for pre-existing films
uv run lanterne sync-reference   # match the CNC official referential against stored venues
uv run lanterne eval-extraction  # score the LLM on the golden dataset
uv run lanterne eval-specialness # score the specialness classifier
uv run lanterne reset-db --yes   # drop + recreate all tables (wipes data)
uv run lanterne --help           # list every command
```

`sync-reference` loads the CNC's official active-establishments census
plus its Île-de-France complement and matches each against stored venues
(see [ADR 0026](decisions/0026-official-reference-data-layer.md)). A match
is only ever applied automatically when name, département, and geocoded
distance all agree — everything else is logged at `WARNING`
(`grep '"msg":"reference match needs review"' logs/app.jsonl`) for manual
confirmation via pgweb, never applied silently. Pass `--dry-run` to preview
what a real run would do (matching and logging, no database write) before
running it for real. Safe to re-run: a venue already matched
(`Venue.cnc_id` set) is skipped.

(`uv run python -m lanterne <command>` still works identically.)

**Re-running is safe**: `scrape` upserts on a deduplication key, so running it
repeatedly never creates duplicates — it refreshes and enriches in place (see
[ADR 0002](decisions/0002-dedup-merge-strategy.md)). Use `reset-db` only when you
want a truly empty database.

`run-bot` answers `/start` (subscribe), `/stop` (unsubscribe), and any other
message as a natural-language question. Schedule `scrape`, `weekly-digest`,
and `prune-db` with cron (or any scheduler) for unattended operation —
`prune-db` deletes ordinary (`is_special=False`) screenings that started more
than 14 days ago, keeping the database bounded now that the width sources
(Paris Ciné Info, offi.fr — see [ADR 0008](decisions/0008-drop-allocine-width-source.md))
report every screening, not only special ones. Special screenings are never
pruned, regardless of age. Run it right after `scrape` in the same weekly
job, as its own step — not auto-chained inside `scrape` — same posture as
`backup-db`.

## Database schema, migrations, and backups

Every CLI command creates or upgrades the schema automatically by running the
**Alembic** migrations (`packages/lanterne/migrations/`) before touching the
database — a fresh checkout needs no manual step. The migration
configuration (`alembic.ini`/`pyproject.toml`) is resolved relative to the
`lanterne` package's own location, not the caller's working directory, so
the CLI works the same from the repository root or from
`packages/lanterne/`.

When you change a model in `core/models.py`, generate the migration alongside
it (never edit the schema by hand). The `alembic` CLI itself (unlike the
`lanterne` CLI) resolves `alembic.ini` relative to the working directory, so
run it from `packages/lanterne/`:

```fish
cd packages/lanterne
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head   # or just run any `lanterne` CLI command, from anywhere
```

PostgreSQL's MVCC handles concurrent access between the polling bot and the
scrape cron natively — no equivalent of SQLite's WAL-mode toggle is needed.
`backup-db` snapshots the live database with `pg_dump --format=custom`
(consistent even while the bot runs, and restorable with `pg_restore`) into
a timestamped file — schedule it next to the weekly scrape. Requires
`pg_dump` on `PATH` (see *Prerequisites*).

## Migrating from an older SQLite database

A repository predating [ADR 0014](decisions/0014-postgres-migration.md) has
its data in a SQLite file rather than PostgreSQL. `scripts/migrate_sqlite_to_postgres.py`
moves it over — idempotent and read-only on the SQLite side:

```fish
# 1. Fresh backup — never migrate from the live file directly. The `lanterne`
#    CLI no longer speaks SQLite (asyncpg-only since ADR 0014), so back up
#    with sqlite3's own backup API rather than `backup-db`:
python3 -c "import sqlite3; sqlite3.connect('lanterne.db').backup(sqlite3.connect('backups/pre-pg-migration.db'))"

# 2. Point DATABASE_URL (env var or .env) at the PostgreSQL target, then
#    dry-run: read-only, reports what would change, writes nothing.
uv run python scripts/migrate_sqlite_to_postgres.py backups/<snapshot>.db --check

# 3. Real run.
uv run python scripts/migrate_sqlite_to_postgres.py backups/<snapshot>.db

# 4. Verify: exit code 0 and every table reports [OK] once the target
#    matches the source exactly (row counts and a per-row content hash).
uv run python scripts/migrate_sqlite_to_postgres.py backups/<snapshot>.db --check
```

Safe to re-run at any point — a row already present in the target (by
primary key) is skipped, never duplicated or overwritten. The source
SQLite file is never modified or deleted by the script; removing it is a
separate, manual decision made only after the row counts and spot checks
above are confirmed.

## Docker

The dev stack (`docker-compose.yml`) runs PostgreSQL, Ollama, pgweb (see
*Inspecting the database*), and the `lanterne` image (bot + scraper) as
separate services, each reachable directly on its own port — no reverse
proxy in dev. `cp .env.example .env` and fill it in first (see
*Configure*); the `DATABASE_URL`/`OLLAMA_BASE_URL` values in `.env` are
only used when running the CLI directly on the host — compose overrides
both to point at the `postgres`/`ollama` services for the containers.

```fish
docker compose up -d postgres ollama   # infra first — waits for both to be healthy
docker compose up ollama-pull          # one-shot: pulls OLLAMA_MODEL into the ollama service
docker compose up -d pgweb             # optional: DB browser at :8082
docker compose up bot                  # long-running Telegram bot
docker compose run --rm scraper        # one-shot: `lanterne scrape`
docker compose run --rm scraper lanterne stats  # any other admin command
```

`postgres` and `ollama` publish to `127.0.0.1` only, on non-default ports
(`POSTGRES_HOST_PORT`/`OLLAMA_HOST_PORT`, defaulting to `5433`/`11435`) so
they don't collide with a locally-run instance of either — override in
`.env` if those are taken too. `ollama` requests an NVIDIA GPU via Compose's
device reservations (`docker compose exec ollama nvidia-smi` to confirm
it's visible inside the container) — requires the NVIDIA Container Toolkit
on the host; drop the `deploy.resources.reservations.devices` block in
`docker-compose.yml` to run CPU-only.

`scraper` is profile-gated (`profiles: ["scraper"]`) so a plain
`docker compose up` never starts it — scraping is triggered on demand or by
cron, not run as a daemon. Both `bot` and `scraper` build from the same
`docker/lanterne.Dockerfile` and wait for `postgres`/`ollama` to report
healthy before starting.

`api` (FastAPI) and `web` (Next.js) are hello-world scaffolding — see
[ADR 0019](decisions/0019-hello-world-api-web.md) — proving the deployment
chain works, no business endpoints yet:

```fish
docker compose up -d api web
curl http://localhost:8001/health   # API_HOST_PORT
open http://localhost:3000          # WEB_HOST_PORT — renders the API's health status
```

### Production

`docker-compose.prod.yml` is an **overlay**, not a standalone file — apply
it on top of the dev file, never alone:

```fish
CADDY_DOMAIN=your-domain.example docker compose \
  -f docker-compose.yml -f docker-compose.prod.yml up -d
```

It adds a Caddy reverse proxy (automatic HTTPS) fronting `web`/`api` on
`80`/`443`, and clears every other service's dev-only host port — nothing
but Caddy is reachable from outside the host. See
[ADR 0020](decisions/0020-prod-compose-overlay.md) for what was verified
(the routing/TLS logic, locally) versus what wasn't (a real deployment —
no server or domain exists yet).

## Observability

Logs go to **two sinks**:

- **Console** — human-readable, colored, with the structured context shown as
  `key=value`. `scrape` additionally renders a live **progress bar per source**.
  Set `LOG_LEVEL=DEBUG` for more detail.
- **`logs/app.jsonl`** — one JSON object per line, for cheap machine/AI debugging:

```fish
grep '"level":"ERROR"' logs/app.jsonl | tail        # only errors
jq 'select(.msg=="question answered")' logs/app.jsonl  # what the Q&A LLM produced
```

Failed LLM extractions and enrichments include the exception traceback in the
`exc` field. Every answered question logs the interpreted `criteria` and the
number of `results`, so an empty answer is easy to diagnose (e.g. the model
resolving a relative date to the wrong window).

## Inspecting the database

`screeningevent` holds the screenings (joined to `film` and `venue`),
`eventsighting` the per-source provenance, and `subscriber` the digest
opt-ins — see [ADR 0006](decisions/0006-relational-schema-split.md) for the
full schema. Connect with `psql` (part of `postgresql-client`, see
*Prerequisites*) using the same `DATABASE_URL` credentials, swapping the
`postgresql+asyncpg://` driver prefix for plain `postgresql://`. Against a
locally-run PostgreSQL:

```fish
psql postgresql://lanterne:lanterne@localhost:5432/lanterne
```

Against the docker-compose `postgres` service instead, use its published
host port (`POSTGRES_HOST_PORT`, default `5433` — deliberately not 5432,
to avoid colliding with a locally-run instance):

```fish
psql postgresql://lanterne:lanterne@localhost:5433/lanterne
```

A dockerized GUI client is also part of the stack —
[**pgweb**](https://github.com/sosedoff/pgweb), not exposed publicly
(`127.0.0.1` only), read-only by default:

```fish
docker compose up pgweb   # browse at http://localhost:8082 (PGWEB_HOST_PORT)
```

See [ADR 0018](decisions/0018-postgres-gui-client-pgweb.md) for why pgweb
rather than the originally-requested Outerbase Studio.

Useful queries:

```sql
SELECT s.source, count(*) FROM eventsighting s GROUP BY s.source;
SELECT f.title, v.name AS venue, e.starts_at, e.has_team_present
FROM screeningevent e
JOIN film f ON f.id = e.film_id
JOIN venue v ON v.id = e.venue_id
ORDER BY e.starts_at LIMIT 20;
SELECT count(*) FROM screeningevent WHERE has_team_present = true;  -- team-present previews
```
