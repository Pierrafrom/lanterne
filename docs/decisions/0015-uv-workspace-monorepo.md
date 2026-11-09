# 0015 — Monorepo with a uv workspace, `packages/lanterne` as the shared domain library

- Status: accepted
- Date: 2026-07-15

## Context

The repository currently holds one Python package (`src/lanterne`) driving
both the scraper and the Telegram bot through a single Typer CLI. The
stack is expanding to host services this single-package layout cannot
express cleanly: a public FastAPI API, a Next.js website, a future mobile
app, and observability tooling (see [`docs/vision.md`](../vision.md) for
the full target shape — described there, not implemented yet).

None of these new services are being built out functionally now — this
decision is about giving the repository a structure that can receive
them without a second, disruptive reorganization later.

Two options were considered for the API specifically:

1. **Loosely-coupled service**: a standalone FastAPI project with its own
   dependencies, talking to Postgres directly with no import from
   `lanterne`. Lower short-term risk (nothing about the existing package
   changes), but means duplicating `Settings`, `Database`, and the
   domain models the moment the API does anything real — the exact
   "two sources of truth" problem `rules/common/oop-design.md` and this
   project's own `EventRepository` split (see
   [ADR](../../CHANGELOG.md)) already push against.
1. **uv workspace**: `packages/lanterne` becomes the shared domain
   library (config, database engine/session management, SQLModel models,
   repositories), and every service (`services/api`, and later anything
   else needing the domain) depends on it as a normal workspace member.

## Decision

Adopt the uv workspace layout:

```
lanterne/
├── pyproject.toml              # workspace root — virtual (tool.uv.package = false):
│                                # members list, shared dev-dependency group,
│                                # shared ruff/mypy baseline
├── uv.lock                     # single lockfile for the whole workspace
├── packages/
│   └── lanterne/                # the existing package, fully self-contained
│       ├── pyproject.toml       # deps, build-system, ruff per-file-ignores
│       │                        # (extends the root baseline), pytest config,
│       │                        # [tool.alembic]
│       ├── alembic.ini
│       ├── src/lanterne/…       # import path unchanged: `lanterne.*`
│       ├── migrations/          # Alembic, travels with its domain package
│       ├── tests/                # colocated: this package's own test suite
│       ├── eval/                 # colocated: golden datasets for its CLI evals
│       └── scripts/              # colocated: one-off admin scripts against its DB
├── services/
│   ├── api/                     # FastAPI, depends on `lanterne`
│   ├── web/                     # Next.js — not a Python workspace member
│   ├── mobile/                  # placeholder + README only (vision)
│   └── observability/           # placeholder + README only (vision)
├── docker/                      # per-service Dockerfiles + entrypoints
├── docker-compose.yml            # dev stack
├── docker-compose.prod.yml       # prod stack (reverse proxy/TLS structure only)
└── docs/
```

`src/lanterne` moves to `packages/lanterne/src/lanterne` verbatim — the
`lanterne.*` import path does not change, so no source file inside the
package needs an import rewritten. `tests/`, `eval/`, and `scripts/` move
alongside it: they are all specific to the `lanterne` domain (its own test
suite, its own CLI evaluation datasets, its own one-off admin scripts), so
colocating them inside `packages/lanterne/` keeps the package genuinely
self-contained — the same shape `services/api` will have once it gets its
own tests. What changes is everything that referenced the old top-level
paths: `pyproject.toml` (split into a workspace root + a package-level
file), `alembic.ini`'s location, the `Dockerfile`'s `COPY` instructions,
the CI workflow, and doc references to `src/lanterne`/`tests/`/`eval/`.

Two code paths that resolved `alembic.ini` and the golden-dataset defaults
relative to the process's **working directory** were made to resolve
relative to **the package's own file location** instead
(`Path(__file__).resolve().parent...`) — a CWD-relative assumption is
exactly the kind of fragility a package boundary should not depend on;
the CLI now behaves identically whether invoked from the repository root,
from `packages/lanterne/`, or from inside a container.

`services/api` will become a real workspace member with `lanterne` as a
path dependency (`tool.uv.sources` — `lanterne = { workspace = true }`,
the workspace's native "depend on a sibling member" syntax) — its
hello-world `/health` endpoint will reuse `lanterne.config.Settings` and
`lanterne.io.db.Database` to ping Postgres, rather than reimplementing a
second settings/connection layer. `services/web` is Next.js — not part of
the uv workspace, it lives alongside it with its own
`package.json`/lockfile.

## Consequences

- One `uv.lock` for every Python package in the repo — dependency
  versions stay consistent across `lanterne`, `services/api`, and any
  future Python service. Installing every member into the shared venv
  needs `uv sync --all-packages --all-groups` — the plain `--all-groups`
  used before this decision only synced the (now virtual, dependency-less)
  root project, a real gotcha of virtual workspace roots worth calling out
  explicitly so it isn't rediscovered by trial and error.
- Adding a new Python service later (e.g. a second worker) is "add a
  member to the workspace," not "figure out how to share code with the
  existing package" — the concern this decision exists to resolve ahead
  of time.
- `ruff`/`mypy` share one baseline at the workspace root
  (`[tool.ruff]`/`[tool.mypy]`), which every member's own `pyproject.toml`
  extends (`extend = "../../pyproject.toml"` for ruff; mypy has no
  per-member override since it reads config from its invocation
  directory, always the repo root in CI/docs) — consistent with
  `rules/common/config-standards.md`'s "extend, don't duplicate"
  principle. `pytest` config is genuinely package-scoped (its own
  `testpaths`/coverage source) and lives in each member's own
  `pyproject.toml`, run via `uv run --directory packages/lanterne pytest`.
- Docker build contexts change: the `Dockerfile` for the `lanterne`
  image now needs the workspace root as its build context (to resolve
  the workspace lockfile) even though it only ships `packages/lanterne`
  in the final image — handled with `.dockerignore` and explicit `COPY`
  paths, not by flattening the workspace back out. The image runs the
  already-synced venv directly (`.venv/bin` on `PATH`) rather than through
  `uv run` at container runtime, which otherwise re-syncs (including dev
  dependencies) on every invocation and fails as the non-root runtime
  user against a venv built while root.
- This is a repository-structure change, not a behavior change: the
  scraper and bot CLI commands, their business logic, and the database
  schema are untouched by this decision alone. The full test suite stayed
  green throughout the move (342 tests, verified after every mechanical
  step, not assumed) — one latent test-isolation gap surfaced by the CWD
  change (`test_eval_extraction_command_scores_the_golden_dataset`
  implicitly depended on a real `.env` file at the repository root) was
  fixed by injecting its required settings via `monkeypatch.setenv`
  instead, rather than papering over it.
