# 0009 — Specialness classification: rules only, no LLM fallback (yet)

- Status: accepted
- Date: 2026-07-13

## Context

With the all-screenings expansion (ADR 0008, `docs/coverage-matrix.md`), two
width sources — the extended Paris Ciné Info scraper and offi.fr — report
every screening at their venues, not only curated special ones. Every source
predating that expansion only ever produces a sighting once committed to a
specific `EventType`, so `EventRepository.ingest` already flags those
`is_special=True` at ingestion (`specialness_reasons=["curated_source"]`). A
width source's ordinary screening starts `is_special=False` and needed a
classification pipeline to upgrade it when it is, in fact, noteworthy.

The plan sketched while designing the width-source expansion (see the
now-superseded wording in `docs/coverage-matrix.md` and `CLAUDE.md`) called
for "a rules cascade plus an LLM fallback classifier for ambiguous cases,"
mirroring the codebase's general LLM-for-unstructured-text pattern
(ADR 0004).

Building the classifier surfaced that this codebase has no free text to feed
an LLM fallback with, for any screening that would reach it:

- offi.fr never scrapes any descriptive text per showing — its programme
  page is a bare `HH:MM` badge grid (see `io/scrapers/offi.py`).
- Paris Ciné Info's blank-`com` showtimes are, by construction, the ones with
  no comment at all (`build_showtime_item` in
  `io/scrapers/paris_cine_info.py` routes a showtime to the LLM path
  precisely when `comment` is non-null — a blank one never reaches
  classification carrying any text).

An LLM only earns its cost over a rule when there is unstructured text a
rule cannot parse. With zero such text arriving at the classifier from any
currently wired-in source, an LLM fallback branch would have no real caller
— speculative infrastructure exercised by nothing, the exact anti-pattern
this project's own coding standards ban (no premature abstraction, no
designing for a hypothetical case with no current instance).

## Decision

Ship `core/specialness.py` as a **pure rules cascade**, no LLM step. Four
independent, cheap, deterministic signals, computed from an event's own
already-loaded `film` and `venue` relationships (two more were added later
the same day — see the 2026-07-13 update below):

| Rule                | Fires when                                                                                                   | Confidence |
| ------------------- | ------------------------------------------------------------------------------------------------------------ | ---------- |
| `team_present`      | `has_team_present` is true                                                                                   | 1.0        |
| `cycle_name`        | a cycle/retrospective name is announced                                                                      | 1.0        |
| `institution_venue` | the venue is one of the four patrimonial institutions                                                        | 1.0        |
| `repertory_rarity`  | the film is ≥3 years old at screening time (a heuristic — an ordinary theatrical run lasts weeks, not years) | 0.7        |

Any rule firing sets `is_special=True`; `specialness_reasons` collects every
fired rule's name (a screening can satisfy more than one), and
`specialness_confidence` takes the strongest fired rule's value. A
screening already `is_special=True` (curated by construction) is never
touched — the classifier only ever upgrades, never downgrades or
overwrites an existing verdict.

~~The classifier runs as a step in `IngestionPipeline._ingest_source`, right
after film enrichment (`_enrich`) and before moving to the next sighting —
not as a separate batch command — because the `repertory_rarity` rule needs
`film.release_year`, which only exists once TMDB enrichment has succeeded.~~
**Superseded by the 2026-07-13 update below**: classification moved to a
single end-of-run batch pass once the two aggregate rules were added. The
underlying reason — re-evaluating a screening left ordinary on an earlier
run once conditions change, rather than requiring its own re-ingest — still
holds; only the mechanism changed.

`core/specialness.py` is a single module, not a `core/specialness/` package
as sketched in the original plan — one rules-cascade function with no
second concern to separate out yet. Revisit if an LLM fallback (or a second
rules family) is later added.

## Consequences

- No LLM cost or hallucination risk added to the ingestion pipeline by this
  change.
- A width-source screening with none of the four signals stays ordinary
  (`is_special=False`) even if a human would recognize it as noteworthy from
  context the rules do not see (an evocative title, a niche genre) — an
  accepted false-negative rate, not a false-positive risk.
- If a future source (or a richer offi.fr/Paris Ciné Info scrape) starts
  carrying real free text for an otherwise-ordinary screening, an LLM
  fallback becomes justified again — add it as a fifth rule-cascade step
  gated on "text present and no rule already fired," not a wholesale
  redesign.
- This revises the "rules cascade plus an LLM fallback" wording in
  `docs/coverage-matrix.md` and `CLAUDE.md`, both updated to point here.

## Update (2026-07-13) — two aggregate rules, and a batch-pass architecture

The four rules above judge a screening from facts already sitting on its own
row. Two further signals discussed with the user need something no single
`ScreeningEvent` carries: a film's footprint *across all of its screenings*.

| Rule                       | Fires when                                                                                            | Confidence |
| -------------------------- | ----------------------------------------------------------------------------------------------------- | ---------- |
| `rare_venue_count`         | the film is showing at ≤3 distinct venues (per ADR 0008's note that venue count is a strong proxy)    | 0.6        |
| `sparse_showing_frequency` | this (film, venue) pair has ≤2 stored showings (a one-off slot, not a normal multi-showing daily run) | 0.5        |

`classify_specialness` takes a new `FilmContext` argument (`distinct_venue_count`,
`weekly_showing_count`) rather than querying the database itself, so it stays
pure — the caller (`pipeline.py`) computes it via two new
`EventRepository` methods, `count_distinct_venues`/`count_screenings`. No
explicit date window: the count is over everything currently stored for that
film, which pruning (14-day retention) and the scrapers' own near-term
discovery horizon already keep bounded to the currently relevant window —
avoids a second, wall-clock-dependent window concept and the test-date
coupling that would come with it.

**Architecture change, found while implementing, not in the original
sketch**: classification moved from *inline, right after each sighting's
enrichment* to a **single batch pass after every scraper in a run has
finished** (`IngestionPipeline._classify_pending_specialness`, evaluating
every currently `is_special=False` row, not only the ones touched this run).

The inline design had a real bug: the first venue ingested for a film that
turns out to be widely released would, at that moment, be the *only* venue
seen so far — `distinct_venue_count=1`, under the rare-venue threshold — and
would wrongly fire `rare_venue_count`. Since the classifier never revisits an
event once `is_special=True` (by design — see "only ever upgrades" above),
that false positive would be permanent, not self-correcting, for exactly the
screenings ingested earliest in each run. Deferring to one pass after every
source has run means the two aggregate rules always see that run's final,
complete counts. `tests/test_pipeline.py`'s
`test_run_never_flags_a_widely_released_film_mid_ingestion` is the regression
test for this.

The four original per-event rules moved into the same batch pass too (rather
than keeping a split "some inline, some batched" design) — harmless for them
since they don't depend on scrape order, and simpler to reason about with one
classification moment per run instead of two.

Remaining known imprecision, accepted: a film with an old, already-special
screening (e.g. a one-off avant-première two years ago) still counts toward
its own `distinct_venue_count` today, since specials are never pruned. Marginal
overcounting by one or two venues rarely changes a threshold-based verdict at
these small thresholds (≤3, ≤2) — flagged here in case the eval harness
(planned next) surfaces it as a real source of misclassification.
