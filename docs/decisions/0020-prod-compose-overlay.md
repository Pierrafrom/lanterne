# 0020 — Production Docker Compose overlay: Caddy reverse proxy, structure verified locally

- Status: accepted
- Date: 2026-07-16

## Context

Every prior compose decision ([0017](0017-dev-docker-compose-stack.md),
[0018](0018-postgres-gui-client-pgweb.md), [0019](0019-hello-world-api-web.md))
explicitly deferred the production shape: "no reverse proxy in dev — Caddy
only makes sense at deployment (TLS, routing prod)." This is that
deployment-time structure — a Compose **overlay**, not a rewrite of the
dev stack, since the user's constraint was "don't add this complexity to
the dev setup."

No prod server or domain exists yet (the target is "one rented server," per
the user's stated scale — see [`docs/vision.md`](../vision.md)), so this
cannot be exercised as a real deployment. What *can* be verified without
one: the Compose merge mechanics (does the overlay actually change what it
claims to?) and Caddy's routing/TLS logic itself (does the reverse proxy
correctly route and terminate TLS?) — both checked directly rather than
left as an assumption.

## Decision

`docker-compose.prod.yml`, applied with
`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`
(never standalone — it only adds `caddy` and clears other services' host
ports, it doesn't redefine them):

- **Caddy** (`docker/Caddyfile`) fronts `web` and `api` on `80`/`443`,
  path-routing `/api/*` to `api:8000` and everything else to `web:3000`,
  with Caddy's automatic HTTPS (Let's Encrypt in production; a local
  self-signed cert when `CADDY_DOMAIN=localhost`, which is what was
  actually tested).
- **Every other service's dev-only host-port publish is cleared** with
  the `!reset []` YAML tag (`postgres`, `ollama`, `pgweb`, `api`, `web`).
  This detail matters and was verified, not assumed: Compose
  **concatenates** list-type fields like `ports` across `-f` files by
  default (docs.docker.com/reference/compose-file/merge/) — simply
  omitting `ports:` in the overlay would silently leave the dev file's
  bindings in place, defeating the entire point of this overlay. Checked
  directly: `docker compose -f ... -f ... config` parsed to YAML and
  inspected per-service confirms only `caddy` publishes ports; every
  other service shows none.
- `CADDY_DOMAIN` is a required variable
  (`${CADDY_DOMAIN:?set CADDY_DOMAIN before deploying}`) — a missing
  domain fails the compose invocation immediately rather than silently
  serving on a wrong or default hostname.

**Verified locally** (`CADDY_DOMAIN=localhost`, real containers, real
requests — not just `config` validation):

- `caddy validate --config docker/Caddyfile` — valid syntax.
- Caddy started, auto-provisioned a local certificate for `localhost`
  (its documented special-case behavior for that hostname), and enabled
  automatic HTTP→HTTPS redirect.
- `https://localhost/api/health` (via `curl --resolve` for correct SNI —
  a bare `Host:` header only affects HTTP routing, not TLS certificate
  selection, which tripped up the first attempt) → the same
  `{"status":"ok","database":"reachable"}` the direct API call returns.
- `https://localhost/` → the same rendered hello-world page `web` serves
  directly, confirming path-based routing sends unmatched paths to `web`,
  not just the `/api/*` prefix to `api`.

**Not verified** (genuinely can't be, without a real server/domain):
real Let's Encrypt issuance (needs a publicly resolvable domain and port
80/443 reachable from the internet for the ACME HTTP-01 challenge), and
production secrets management — `docker-compose.yml`'s `env_file: .env`
pattern is a dev convenience, explicitly flagged in this file's own
header comment as needing review before a real deployment.

## Consequences

- Cutting over to a real server is "point `CADDY_DOMAIN` at the real
  domain, set up DNS, run the two-file compose command" — not "design the
  reverse-proxy topology from scratch," since the routing/TLS logic is
  already proven, just not against a public domain.
- `pgweb` and `ollama` ship in the prod overlay with no host port at all
  (not even loopback) — reachable only from other containers on the
  compose network. Admin access to `pgweb` in a real deployment needs an
  SSH tunnel to the server (`ssh -L 8082:localhost:5433 ...` then connect
  locally to the still-loopback-bound `postgres`, or tunnel to `pgweb`
  directly if it's brought up) — not routed through Caddy, deliberately
  (a DB browser is not something to expose on the public internet even
  behind a proxy).
- The dev stack (`docker-compose.yml` alone) is completely unaffected —
  no reverse proxy, no TLS, every service still on its own loopback port,
  exactly as constrained from the start.
