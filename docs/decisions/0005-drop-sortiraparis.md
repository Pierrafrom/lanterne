# 0005 — Drop Sortir à Paris from the MVP

- Status: accepted
- Date: 2026-06-25

## Context

Sortir à Paris (sortiraparis.com) was one of the four sources originally
planned. Probing it against the scraping decision tree
([`docs/scraping-strategy.md`](../scraping-strategy.md)) showed it is a poor fit:

- It is a classic-HTML **editorial news site**, not an events platform. The
  `/loisirs/cinema` hub lists news articles dominated by streaming/TV content
  ("… sur Netflix", "… sur France 2"), not Paris special screenings.
- There is **no structured screenings agenda**: the only JSON-LD block is the
  publisher's `NewsMediaOrganization` metadata, and every probed targeted
  section (`/avant-premiere`, `/cine-concert`, `/cinema-en-plein-air`,
  `/tag/avant-premiere`) returned 404 or redirected to the home page.
- Extracting screenings would mean running the LLM over many irrelevant prose
  articles, with low precision, high token cost, and a real risk of fabricated
  or duplicate events.

## Decision

Drop Sortir à Paris. The MVP ships with three sources, which already cover the
target categories:

- **Première Projo** — avant-premières, including team-present (`AVPE`).
- **La Cinémathèque française** — retrospectives and heritage.
- **Le Forum des images** — thematic cycles and events.

Concretely, the `SORTIRAPARIS` value was removed from the `Source` enum. With no
remaining unimplemented source, the `PendingScraper` placeholder became dead
code and was removed too; the scraper registry now holds exactly the three real
scrapers.

## Consequences

- Lower coverage of one-off ciné-concerts / open-air screenings that Sortir à
  Paris aggregates editorially — accepted for the MVP.
- The scraper registry and `Source` enum are fixed at three sources; adding a
  fourth later means re-introducing an enum value and a scraper (and, if a
  graceful placeholder is wanted again, re-adding a small stub).
- This decision revises the four-source assumption in
  [ADR 0003](0003-scraping-strategy.md) and [ADR 0004](0004-rsc-extraction-and-pipeline.md).
