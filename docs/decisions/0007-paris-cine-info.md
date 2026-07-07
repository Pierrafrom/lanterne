# 0007 — Paris Ciné Info: an authenticated, hybrid-level source

- Status: accepted
- Date: 2026-07-07

## Context

Paris Ciné Info (`paris-cine.info`) was initially evaluated and dropped during
the source recon in [`scraping-strategy.md`](../scraping-strategy.md): it
aggregates exactly the right categories (rétrospectives, événements) across
dozens of Paris cinemas, but every screening is gated behind a mandatory
account login — a different, more sensitive case than a public HTML page, not
pursued without the account holder's explicit say-so.

The project owner has a personal account on the site and authorized using its
credentials for this bot. That changes the calculus: the remaining questions
are technical (what does authenticated access actually expose?) and about
respectful use (frequency, and how the site's stated content-reuse
preferences apply), not about consent.

## What authenticated access actually exposes

Investigating with a real logged-in session (not just reading rendered HTML)
found the site is built on a genuine JSON API, not server-rendered markup:

- `get_movies.php?events=true` — every film the site currently flags as a
  special screening (a real, curated signal: silent classics, festival
  avant-premières, themed nights — not just "old film", confirmed by
  inspecting the actual titles returned).
- `get_showtimes.php?mov_id=…` — that film's showtimes **across every partner
  cinema in Paris at once** (exact datetime, venue name, a direct booking
  link), each carrying a free-text `com` field when the screening is notable
  — e.g. "Avant-première Festival des Cinémas Indépendants Parisiens,
  projection présentée par…", "sera présentée par [a named critic]".

This is neither a clean Level 2 (the `com` field still needs interpreting)
nor a Level 4 source (discovery is a real JSON API, not scraped HTML) per
[`scraping-strategy.md`](../scraping-strategy.md)'s decision tree — it is a
**hybrid**: Level 2 discovery (film, venue, exact time, booking link — no LLM)
feeding a Level-4-style LLM classification of the `com` text alone (into
`event_type`, `cycle_name`, `has_team_present`). A showtime with a blank
`com` is an ordinary screening, even for a film flagged as an "événement",
and is not ingested — the comment is the "why this matters" signal the whole
project exists to surface.

## Decision

Implement `ParisCineInfoScraper` (`io/scrapers/paris_cine_info.py`) as this
hybrid: log in once per run, fetch the flagged films and their showtimes, build
one `RawListing` per commented showtime, and reuse the existing
`structure_via_llm`/`gather_events` pipeline unchanged. The source is entirely
optional — `build_scrapers` omits it when `PARIS_CINE_INFO_LOGIN` /
`PARIS_CINE_INFO_PASSWORD` are not configured, so the bot works without an
account and this never becomes a hard dependency for anyone else running it.

This is also the first scraper to populate `booking_url` (declared on
`Sighting`/`ScreeningEvent` since ADR 0006 but never wired up before): the
API gives a direct per-showtime booking link, so `RawListing` gained a
`booking_url` field threaded through `structure_via_llm`.

### Respectful use

- One login + one pass over the flagged films per weekly scrape — matching
  every other source's cadence, not a polling loop.
- The site's `robots.txt` is not a traditional crawl-permission file but the
  emerging "content signals" format reserving rights under EU Directive
  2019/790 over `ai-train` / `ai-input` reuse; it does not state a signal
  either way for this project's use (personal, authenticated, processed by a
  **locally-run** Ollama model, not sent to a third-party AI service, and not
  republished as generated summaries). Noted here for transparency rather
  than resolved unilaterally — revisit if the site publishes an explicit
  policy or if the account holder's relationship with the site changes.

## Consequences

- One integration now covers dozens of venues (Le Grand Action, MK2, Luminor,
  Studio des Ursulines, indies…) with genuinely curated content, likely
  exceeding the combined value of several single-venue scrapers — it was
  reprioritized ahead of the individually-recon'd venues in
  `scraping-strategy.md`.
- The account credentials are a new kind of secret (not an API key): a
  personal login, kept in `.env` only (never `.env.example` values, never
  logged) and used solely to authenticate this scraper's own session.
- If the account is ever suspended or credentials rotated, the source fails
  loudly (`RuntimeError` on a rejected login) rather than silently returning
  zero events — surfaced via the admin Telegram report
  ([ADR](../architecture.md#ingestion-flow)) as a scrape failure, not a quiet
  "0 séance(s)".
- Single-venue Level-4 scrapers (Le Champo, Le Louxor, …) remain worth
  building afterward: they carry editorial context (a named ciné-club host,
  a cycle's curatorial framing) that a generic aggregator's `com` field may
  not always capture, and cross-source `EventSighting` provenance benefits
  from more than one source per screening.
