# Observability (placeholder)

Not implemented. See [`docs/vision.md`](../../docs/vision.md) for the
target shape: metrics (API latency/error rate, scrape run duration and
per-source success/failure) and dashboards once there are multiple
long-running services (API, bot, scrape cron, website) instead of one bot
process, joining the structured JSONL logging already in place
(`rules/common/logging.md`).

No tool is chosen yet (candidates: Grafana + Prometheus/Loki, or a hosted
alternative, consistent with `rules/devops/devops.md`'s
OpenTelemetry-first stance once this is actually built). This directory
is reserved so that choice has a home without disrupting the rest of the
monorepo layout — see
[ADR 0015](../../docs/decisions/0015-uv-workspace-monorepo.md).
