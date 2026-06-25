# 0002 — Cross-source deduplication merge strategy

- Status: accepted
- Date: 2026-06-25

## Context

The same special screening is regularly announced on more than one of the four
scraped sources (e.g. an avant-première listed on both premiereprojo.fr and
sortiraparis.com). The weekly digest must show it once, not four times.

ADR 0001 established a deterministic `dedup_key` derived from a screening's
intrinsic identity (film + venue + start time). What remains is the *merge
strategy*: what happens when a freshly scraped event collides with one already
stored under the same key.

## Decision

`EventRepository.upsert` is the single entry point for persisting scraped
events. On key collision it applies a **first-seen-wins, later-enriches**
policy:

| Field                                               | On collision                                              |
| --------------------------------------------------- | --------------------------------------------------------- |
| `id`, `source`, `source_url`                        | kept from the first-seen row (stable provenance)          |
| `tmdb_id`, `overview`, `poster_url`, `release_year` | kept (enrichment is not re-run)                           |
| `title`, `venue`, `starts_at`, `event_type`         | kept (they define the key, so they match by construction) |
| `has_team_present`                                  | logical OR of both reports                                |
| `description`                                       | kept if present, otherwise backfilled from the newcomer   |

Rationale for the two enriching rules: team presence is the priority signal for
avant-premières, so if *any* source confirms it we keep `True`; and a missing
description is worth filling from whichever source happens to provide one.

## Consequences

- Idempotent ingestion: re-scraping the same source, or scraping a second
  source, never creates duplicates and never regresses the enriched fields.
- Provenance points to the first source that found the screening; we do **not**
  track the full set of sources. Recording every source that reported an event
  is deferred until there is a concrete need (YAGNI) — it would require a
  separate source-links table.
- The merge is intentionally conservative: it never overwrites an existing
  description, to avoid a lower-quality source clobbering a richer one.
