# 0003 — Scraping strategy: thin scrapers, LLM does the structuring

- Status: accepted
- Date: 2026-06-25

## Context

The bot aggregates special screenings from four sources. A survey of their live
markup showed they fall into two very different shapes:

- **Classic server-rendered HTML** — cinematheque.fr, forumdesimages.fr. Event
  data is in the delivered HTML and parseable with CSS selectors.
- **Next.js RSC payloads** — premiereprojo.fr, sortiraparis.com. The content is
  present but embedded in escaped `self.__next_f.push([...])` script blobs, with
  no stable DOM to select against.

Each source therefore needs bespoke, fragile parsing logic, and a change to any
site can silently break its scraper. We need an architecture that contains that
fragility and a realistic incremental rollout.

## Decision

**Scrapers stay thin; the LLM does the structuring.** A scraper's only output is
a list of `RawListing` (source, source_url, raw_text) — one blob of
human-readable text per screening. Turning that text into a typed, validated
`ExtractedEvent` is the LLM extractor's job (ADR-independent, see `io/llm.py`).
This isolates the brittle, site-specific concern (locating text) from the stable
one (understanding it), and means a site redesign at worst changes which text we
gather, never how it is interpreted.

**Sources plug in via a `SourceScraper` protocol and a registry.** Adding a
source means writing one class and appending it to `SCRAPERS` in
`io/scrapers/__init__.py`; the ingestion pipeline iterates the registry without
knowing any source by name (OCP).

**Network and parsing are split.** Each scraper exposes pure `parse_*(html)`
methods (unit-tested against committed, trimmed real-markup fixtures) and an
`async fetch_listings(client)` that only orchestrates HTTP. Tests never touch
the network.

**Incremental rollout.** Only La Cinémathèque française is fully implemented for
the MVP — its homepage lists screenings as `a.event` cards linking to
`/seance/` detail pages, and each detail page carries the authoritative date,
room, cycle, and film (so the scraper reads the index for links, then each
detail page for text). The other three sources are registered as
`PendingScraper` placeholders that yield nothing and log a warning, so the
pipeline runs end to end today and gains sources without structural change.

## Consequences

- The fragile part of the system is small, per-source, and behind a uniform
  interface; a broken scraper degrades to "no listings from that source"
  rather than crashing the run.
- The Next.js sources (Première Projo, Sortir à Paris) will likely need a
  different extraction tactic than CSS selectors — parsing the embedded RSC/JSON
  payload, or an internal JSON endpoint. That work is deferred (M5b) and does
  not affect the interface.
- Trimmed HTML fixtures must be refreshed if a site's markup changes; a failing
  parse test is the intended early-warning signal.
- LLM cost scales with the number of listings (one extraction call each), which
  the deduplicating upsert (ADR 0002) bounds across re-runs but not within a
  single run.
