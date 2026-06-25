# 0001 — Split the event into three distinct types

- Status: accepted
- Date: 2026-06-25

## Context

A special screening flows through the system in three different roles:

1. It is **extracted** by an LLM from a scraped announcement (raw facts only).
1. It is **persisted** in SQLite, with provenance and a deduplication key.
1. It is later **enriched** with TMDB data (synopsis, poster, release year).

SQLModel makes it tempting to model all of this with a single
`table=True` class that doubles as the LLM output schema. That single class
would carry fields the LLM must never produce (`id`, `dedup_key`, `tmdb_id`),
forcing every field to be optional and pushing validation responsibility onto
the caller — an Anemic Domain Model with a leaky contract.

## Decision

Use three types with clear, separate responsibilities:

- `ExtractedEvent` (plain Pydantic `BaseModel`): the LLM output contract. Holds
  only what is readable from an announcement (`title`, `event_type`, `venue`,
  `starts_at`, `has_team_present`, `description`). No identity, no provenance,
  no enrichment.
- `ScreeningEvent` (`SQLModel`, `table=True`): the persisted row. Adds the
  surrogate `id`, the unique `dedup_key`, the `source`/`source_url` provenance,
  and the optional TMDB enrichment fields.
- `ScreeningEvent.from_extracted(...)`: the single factory bridging the two. It
  computes the deduplication key and copies extracted fields verbatim, leaving
  TMDB fields empty until the enrichment step.

The deduplication **key computation** (`core/dedup.py`) is a pure function
derived only from a screening's intrinsic identity (title + venue + start
time), so the same event announced on two sources collapses to one key. The
deduplication **merge strategy** (insert-or-update on key collision) belongs to
the persistence layer, not here.

## Consequences

- The LLM is asked for a tight, fully-required schema — better structured-output
  reliability and no optional-field noise.
- The persistence model owns identity and enrichment, keeping the extraction
  contract stable even if the storage schema evolves.
- One extra factory method to maintain, and a deliberate field duplication
  between `ExtractedEvent` and `ScreeningEvent` — accepted as the cost of
  decoupling the two contracts.
