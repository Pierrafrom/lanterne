# 0027 — Field-level provenance and source trust tiers

- Status: proposed
- Date: 2026-08-03
- Supersedes the merge policy of [ADR 0002](0002-dedup-merge-strategy.md)

## Context

[ADR 0002](0002-dedup-merge-strategy.md) settled cross-source merging with a
**first-seen-wins, later-enriches** policy: the first source to report a
screening owns its identity, subsequent sources may only fill nulls, plus
two hardcoded exceptions (`has_team_present` and `is_special` merge as a
logical OR).

That policy was correct for its context — four curated sources, all of
comparable quality, all reporting special screenings only. It has since
been stretched well past its design point:

1. **Sources are no longer comparable in quality.** The project now spans
   an authenticated JSON API (Paris Ciné Info), a tabular HTML parse
   (offi.fr), and LLM extraction over free prose (La Cinémathèque, Forum
   des images). ADR 0002's own closing note — "it never overwrites an
   existing description, to avoid a lower-quality source clobbering a
   richer one" — already identifies quality as the real criterion, then
   approximates it with arrival order.
1. **Arrival order is not a quality contract.** It is the iteration order
   of the scraper registry in `io/scrapers/__init__.py`. Nothing in the
   type system, the tests, or the documentation prevents a reordering from
   silently changing which source's value ends up stored.
1. **The rule is unenforceable across passes.** `_apply_venue_passes`,
   `_apply_venue_details` and `_apply_film_ratings` write venue and film
   fields *outside* the merge path entirely, each with its own ad hoc
   overwrite behaviour.
1. **[ADR 0026](0026-official-reference-data-layer.md) breaks it outright.**
   Two official datasets of different vintages (a current CNC census with
   no addresses, a ~2022 IDF file with addresses and labels) write to the
   same `Venue` row. Under first-seen-wins, whichever loads first wins —
   an arbitrary outcome for the one layer that is supposed to be
   authoritative.

There is also no way, today, to answer "where did this address come from,
and when?" for any row in the database — the very question that made the
`needs_review` audits ([ADR 0025](0025-venue-address-research-completion.md))
expensive manual work.

## Decision

Replace order-based merging with **explicit source trust tiers plus
per-field provenance**.

### 1. Trust tiers

A new `SourceTier` enum ranks *how a fact was obtained*, orthogonally to
*which site it came from* (`Source` stays as-is, and gains a mapping into
this enum):

| Tier | Name                    | Meaning                                           | Members today                                                |
| ---- | ----------------------- | ------------------------------------------------- | ------------------------------------------------------------ |
| 0    | `OFFICIAL_REGISTRY`     | A public authority's own registry                 | CNC establishments, CNC visas, BAN/Géoplateforme             |
| 1    | `FIRST_PARTY`           | The venue or distributor publishing about itself  | Cinémathèque, Forum des images, Fondation Pathé, La Villette |
| 2    | `STRUCTURED_AGGREGATOR` | A third party serving machine-readable data       | Paris Ciné Info API, TMDB, Première Projo RSC                |
| 3    | `PARSED_MARKUP`         | Deterministic parse of HTML, no model in the loop | offi.fr                                                      |
| 4    | `MODEL_EXTRACTED`       | An LLM read prose and produced the value          | any `structure_via_llm` output                               |

The tier is a property of the *extraction path*, not of the site: a
first-party venue site read through the LLM contributes its `starts_at` at
tier 4, while the same scraper's deterministically-resolved date
(`core/frenchdate.py`, see [ADR 0012](0012-retire-lechampo.md)) contributes
at tier 1. This is the existing "known-true facts win" posture from
`structure_via_llm`'s `known_starts_at`/`known_venue` parameters,
generalized from two special cases into the model.

### 2. Merge policy per field kind, not per field

Three policies cover every field; each field is assigned one, declared in
one table read by both the repository and the tests:

- **`BEST_SOURCE`** — lowest tier wins; ties broken by most recent
  observation. For factual attributes with one correct value: `address`,
  `location`, `seat_count`, `website`, `starts_at`, `runtime_minutes`.
- **`ANY_TRUE`** — a positive assertion from any source wins and is never
  retracted by silence. For `has_team_present` and `is_special`: a source
  that does not mention the team is not asserting its absence. This
  preserves ADR 0002's two exceptions and, for the first time, explains
  *why* they are exceptions rather than oversights.
- **`FIRST_NON_NULL`** — first non-empty value wins, no overwriting. For
  free text where "different" does not mean "better": `description`,
  `cycle_name`.

### 3. Storage: a `provenance` JSONB column on `Venue` and `Film`

`Venue.provenance` and `Film.provenance` map field name → `{source, tier, observed_at}`. Chosen over a separate `field_provenance` table because the
volume is small (hundreds of venues, thousands of films), the data is
always read and written with its owning row, and it adds no join to any
existing query. The project already has JSONB conventions and a documented
gotcha to respect (`none_as_null=True`, see
[ADR 0023](0023-jsonb-none-as-null-bug.md)).

`ScreeningEvent` gets no `provenance` column: `EventSighting` already
records every source's report of a screening
([ADR 0006](0006-relational-schema-split.md)), and no official source of
showtimes exists to arbitrate between them anyway
([ADR 0026](0026-official-reference-data-layer.md)). Screening fields are
resolved by tier at merge time and audited through the sighting rows.

### 4. The resolution rule is a pure function

`core/provenance.py` exposes a single pure decision function — given the
current value with its provenance, a candidate value with its provenance,
and the field's policy, should the candidate be written? No I/O, no
session, fully unit-testable, and the single place the policy exists. Every
write path routes through it: `EventRepository._merge`,
`VenueRepository.resolve`, `update_venue_details`, `update_venue_passes`,
`update_film_ratings`, and the new reference loaders. A write path that
bypasses it is a bug the tests should catch.

## Consequences

- **A scraper can never degrade an official fact.** The core property this
  ADR exists to guarantee: a tier-3 offi.fr address cannot overwrite a
  tier-0 CNC one, regardless of load order, scraper registry order, or
  which pass runs last.
- **Backfilling still works upward.** A tier-0 source arriving after a
  tier-4 value *replaces* it, which first-seen-wins explicitly forbade.
  This is the intended behaviour change and the mechanism by which
  [ADR 0026](0026-official-reference-data-layer.md)'s referential cleans up
  years of accumulated scraped guesses without a bespoke migration script.
- **Provenance becomes queryable.** "Which venue addresses still come from
  an LLM or a name search?" becomes a JSONB query instead of a manual
  audit — directly addressing the cost of ADR 0025's work.
- **Scraper registry order stops mattering** for correctness. It still
  matters for `EventSighting` insertion order and thus for which source's
  `source_url` a screening displays first; that is cosmetic and stays as
  is.
- **Migration is not free.** Existing rows have no provenance. They are
  backfilled to the tier of whichever `Source` last touched them where that
  is inferable, and to `MODEL_EXTRACTED` (the most pessimistic tier) where
  it is not — so an unknown-origin value is always replaceable by a better
  one, never accidentally protected.
- **A tier can be wrong.** An official registry can contain an error a
  scraper would have got right. The tiers encode expected reliability, not
  a guarantee; the `needs_review` escape hatch stays, and provenance makes
  the disagreement visible rather than silently resolving it.
