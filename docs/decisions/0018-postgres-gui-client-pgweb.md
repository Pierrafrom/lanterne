# 0018 — Dockerized PostgreSQL GUI client: pgweb, after Outerbase Studio proved unusable

- Status: accepted
- Date: 2026-07-16

## Context

The project needs a dockerized PostgreSQL GUI client for local inspection
(see [0014](0014-postgres-migration.md)'s "not exposed publicly" clause,
[`docs/vision.md`](../vision.md)). The user's first choice was Outerbase
Studio, specifically, and asked for a self-built image (from a clean base,
not a third-party pre-built one) if it had to be dockerized by hand.

## What was tried

**Outerbase Studio**, investigated and built end to end before being
ruled out:

1. **No official Docker image.** `outerbase/studio`'s own GitHub
   discussion #443, requesting exactly this (Docker + Postgres via env
   vars), has had zero replies since it was opened in May 2025. Only
   unofficial third-party images exist on Docker Hub — an unaudited
   supply-chain risk for an image that would hold database credentials.
1. **No tagged release since `v0.10.2`** (March 2025, pre-acquisition by
   Cloudflare — announced April 2025, hosted cloud shut down October
   2025). `develop` is still actively committed to (verified: a commit as
   recent as 2026-03-01, Dependabot keeping Next.js current), so a custom
   image was built by fetching the source at a **pinned commit SHA**
   (`b06fb85e5967440278d5a815721b360920566ab9`) rather than an untrusted
   binary or a stale year-old tag — using the project's own official
   `Dockerfile` recipe (`node:20-alpine`, Next.js `output: "standalone"`)
   as the base, adapted to `git fetch --depth 1 origin <sha>` the source
   at build time instead of assuming a local checkout.
1. **The image built and ran cleanly** (360 MB, non-root user, `ENV HOSTNAME="0.0.0.0"` set explicitly — Next.js standalone's `server.js`
   otherwise binds to localhost only and is silently unreachable despite
   a correct Docker port mapping).
1. **Postgres connectivity does not work — confirmed empirically, not
   just documented.** The project's own docs already hinted at this
   ("browsers cannot open a raw Postgres connection... Outerbase Studio
   Desktop [an Electron wrapper] enables support for drivers that
   aren't feasible in a browser environment, such as MySQL and
   PostgreSQL"), but the deciding evidence was direct: the running
   container's own client-side JavaScript bundle contains the literal
   string `"postgres"===e.dialect?{_tag:"SUCCESS",value:"Postgres dialect is not supported yet"}`. Postgres is explicitly gated off in
   the web build, not merely undocumented or in-progress.

The build artifacts for this attempt were removed (no dead code kept
around); this ADR is the record of the investigation.

## Decision

Use **pgweb** (`sosedoff/pgweb`) instead — an officially maintained,
Postgres-native, lightweight web GUI with no such ambiguity, pinned to
`0.17.0` (explicit PostgreSQL 18 support, current as of this decision).
Run with `--readonly` by default (pgweb's own app-level guard against
write statements — not a substitute for real PostgreSQL role-based
permissions, but a sensible default for a tool whose stated purpose is
"consult the data," not modify it). Bound to `127.0.0.1` only, on a
non-standard default host port (`PGWEB_HOST_PORT`, default `8082`,
avoiding a same-class collision with another locally-bound service —
see [0017](0017-dev-docker-compose-stack.md) for the same pattern already
applied to `postgres`/`ollama`'s own host ports).

Verified end to end against the stack's real, migrated Postgres data:
`/api/objects` lists all seven tables, a real query against
`screeningevent` returns the correct row count (41,231, matching
[0014](0014-postgres-migration.md)'s migrated dataset exactly), and a
`DELETE` statement is correctly rejected by read-only mode.

While making this change, `ollama/ollama` was also pinned to `0.32.0`
(was `:latest`) for the same reproducibility reason
(`rules/devops/devops.md`: "Pin images by digest, not by tag — latest is
mutable and unpredictable").

## Consequences

- The dev stack's DB-browsing story is pgweb, not Outerbase Studio — a
  real scope change from the original ask, made only after empirical
  verification (not just documentation reading) showed the requested tool
  cannot do the one thing it would be needed for.
- pgweb's read-only mode is an application-level guard, not a database
  permission — if a hard guarantee against writes is ever needed (e.g.
  granting broader access to this tool later), a dedicated read-only
  PostgreSQL role is the correct mechanism, not reliance on this flag
  alone.
- If Outerbase Studio's web build ever adds real Postgres support
  upstream, revisit this decision — the investigation above (particularly
  the pinned-commit Dockerfile pattern) is directly reusable.
