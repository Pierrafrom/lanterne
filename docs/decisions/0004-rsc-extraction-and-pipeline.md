# 0004 — Next.js RSC extraction and the scraper output contract

- Status: accepted
- Date: 2026-06-25
- Supersedes part of [0003](0003-scraping-strategy.md) (the scraper output contract)

## Context

ADR 0003 set scrapers up to emit `RawListing` (raw text) and have the LLM
structure every listing. Inspecting the live Next.js sources changed the
picture: premiereprojo.fr embeds **fully structured JSON** for each screening
inside its `self.__next_f.push([...])` RSC payloads — title, director, synopsis,
release date, cinema name, `avpType` ("AVP" = avant-première), ticket link, even
a `scrapedAt` timestamp.

Two consequences follow. First, scraping these sites does not need a headless
browser. Second, running the LLM over data that is *already* structured would
waste tokens and, worse, reintroduce non-determinism and hallucination risk on
clean data — a regression, not a feature.

## Decision

### RSC sites: extract the embedded JSON, no browser, no LLM

For Next.js sources, parse the JSON embedded in the RSC stream rather than the
rendered DOM. The fragile surface moves from generated Tailwind CSS classes to
the site's **business JSON schema**, which is markedly more stable. Playwright
is explicitly rejected: it is heavy (a ~300 MB browser, slow, awkward in CI) and
here less reliable than the JSON it would render. These sources map their JSON
straight to `ExtractedEvent` with **no LLM call**.

### Scrapers output events, not text

The right shared abstraction is the scraper's **output**, not its input. The
`SourceScraper` protocol is therefore `fetch_events(client) -> list[ScreeningEvent]`.
How a scraper gets there is internal:

- A text source (cinematheque.fr) composes an injected `EventExtractor` (LLM) —
  the LLM is now an implementation detail of text-based scrapers, not a pipeline
  stage.
- A structured source (the Next.js sites) maps JSON directly.

`RawListing` survives only as an internal carrier inside text-based scrapers.
Scrapers are built by `build_scrapers(extractor)`, which injects the extractor
where needed (DIP). The `IngestionPipeline` iterates scrapers and upserts their
events, fully agnostic to the text-vs-structured distinction (OCP): adding
either kind of source never touches the pipeline.

### Pipeline resilience and observability

A source whose scrape raises is logged and skipped, never aborting the run; the
run returns an `IngestionReport` (events ingested, sources failed) and logs a
structured per-source and final summary.

## Consequences

- No browser dependency; the toolchain stays httpx + BeautifulSoup + the JSON
  parser for RSC sites.
- LLM cost is incurred only for genuinely unstructured sources, bounding tokens
  and removing a class of extraction errors on the structured ones.
- The M5 contract (`fetch_listings -> RawListing`) was refactored while only one
  real scraper existed — cheap now, expensive later.
- Implementing the three pending sources (M5b) plugs into the same protocol with
  no pipeline change; the Next.js ones will carry an RSC-JSON parser and skip the
  extractor entirely.
