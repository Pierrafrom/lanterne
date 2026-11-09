# 0019 — Hello-world FastAPI + Next.js, validating the full deployment chain

- Status: accepted
- Date: 2026-07-16

## Context

[ADR 0015](0015-uv-workspace-monorepo.md) reserved `services/api` and
`services/web` for the public API and website described in
[`docs/vision.md`](../vision.md), without building anything functional
yet. This decision is that hello-world: no business endpoints, no pages
beyond a status check — purpose-built to prove the deployment chain
actually works end to end (API ↔ PostgreSQL, web ↔ API) before any real
feature work starts on either service.

## Decision

**`services/api`** — a FastAPI app, a real workspace member
(`tool.uv.sources` → `lanterne = { workspace = true }`, per
[ADR 0015](0015-uv-workspace-monorepo.md)'s plan). `GET /health` reuses
`lanterne.config.Settings` and `lanterne.io.db.Database` to run `SELECT 1`
against PostgreSQL, rather than a second settings/connection layer.
Verified live: `curl http://localhost:8001/health` →
`{"status":"ok","database":"reachable"}`.

**`services/web`** — Next.js (App Router, TypeScript strict — the five
flags beyond bare `strict: true` from `rules/typescript/typing-strict.md`
are all enabled), scaffolded with `create-next-app`. The homepage is an
async Server Component that fetches the API's `/health` server-side
(`API_URL=http://api:8000` inside the compose network) — a server-to-server
fetch, not a browser `fetch`, so no CORS configuration is needed. Verified
live: the rendered page shows "API status: ok — database: reachable".

Both are dockerized (`docker/api.Dockerfile`, `docker/web.Dockerfile`) and
wired into `docker-compose.yml`, each on its own host port
(`API_HOST_PORT`/`WEB_HOST_PORT`, defaulting to `8001`/`3000` — `8001` not
`8000`, which was already bound by an unrelated process on the dev
machine, the same collision-avoidance pattern as
[0017](0017-dev-docker-compose-stack.md)'s `postgres`/`ollama` ports).

`services/mobile/` and `services/observability/` got placeholder READMEs
only (per [`docs/vision.md`](../vision.md)) — no code, no framework
choice made yet.

## Notable friction, fixed along the way

- **Biome 2.x's config format changed from what
  `rules/typescript/lint-strict.md`'s template documents**
  (`extends: ["biome:recommended"]` no longer resolves; the current
  mechanism is
  `linter.rules.preset: "recommended"`, generated via `biome init` against
  the actually-installed version rather than assumed). Flagged for the
  user to decide whether to update that rule file; not changed as part of
  this ADR.
- **Biome's `useLiteralKeys` rule structurally conflicts with TypeScript's
  `noPropertyAccessFromIndexSignature`** (required by this project's
  strict baseline) for any value typed via an index signature — `process.env`
  and CSS Modules both are. Disabled `useLiteralKeys` in `biome.json`
  rather than loosening the TypeScript flag, since the strict-typing
  baseline is the more foundational, deliberately-adopted convention.
- **pnpm's build-script approval gate blocked `sharp`** (Next.js's image
  optimization dependency) both locally and inside the Docker build — the
  Docker build failed identically until `pnpm-workspace.yaml` (which
  records the `pnpm approve-builds sharp` decision) was copied into the
  image alongside `package.json`/`pnpm-lock.yaml`.
- **`httpx` (already a core dependency of `packages/lanterne`, used by
  every scraper and the TMDB client) is effectively unmaintained** — no
  releases since 2024, the ecosystem (including Starlette's own
  `TestClient`, which now warns on plain `httpx`) is moving to `httpx2`.
  Flagged for the user; **not migrated** as part of this ADR — that would
  be a significant, invasive change to `packages/lanterne`'s scraping/TMDB
  code, well outside hello-world scope, and deserves its own planned
  migration rather than being bundled in silently here.

## Consequences

- The monorepo now has a genuinely working example of a second Python
  workspace member depending on `lanterne` — the pattern
  [ADR 0015](0015-uv-workspace-monorepo.md) anticipated is proven, not
  just documented.
- CI gained a second job (`web-quality`: biome, `tsc --noEmit`, `next build`)
  alongside the existing Python one, and pre-commit gained matching local
  hooks (`mypy-api`, `biome-web`) — every workspace member has a lint/type
  gate, not just `packages/lanterne`.
- No functional API endpoints or website pages exist yet — the next real
  work on either service starts from a confirmed-working skeleton, not an
  untested one.
