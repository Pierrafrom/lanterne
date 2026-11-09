# 0026 — Official reference-data layer, separate from the programme layer

- Status: accepted
- Date: 2026-08-03
- Implemented: 2026-08-03 (phase 1 — see "Phase 1 implementation notes"
  below for scope decisions made during implementation)

## Context

Every fact Lanterne stores today originates from a *programme* source: an
aggregator (Paris Ciné Info, offi.fr) or a venue's own site. Venue identity
is derived from whatever name a scraper happened to announce, normalized
into `Venue.slug` (see [ADR 0006](0006-relational-schema-split.md)). That
choice is the root cause of a long, individually-patched list of defects,
all recorded in [`docs/coverage-matrix.md`](../coverage-matrix.md):

- MK2's venue-name fragmentation, healed lazily via
  `Venue.paris_cine_info_tid` ([ADR 0011](0011-retire-mk2-louxor.md)).
- La Cinémathèque's rooms masquerading as venues, fixed by a one-off script
  ([ADR 0021](0021-venue-screen-split-and-geocoding.md)).
- Eight `needs_review` venues whose geocoded coordinates were not merely
  imprecise but in the wrong department
  ([ADR 0025](0025-venue-address-research-completion.md)).
- Three real "La Grange" cinemas conflated into one row, fixed by adding a
  second per-source external id (`Venue.offi_slug`)
  ([ADR 0024](0024-venue-data-quality-cleanup.md)).
- Five IDF venues absent from the database entirely, added by hand via
  `scripts/add_missing_idf_venues.py`, with the explicit caveat that
  offi.fr's directory "is the best single source checked so far, not a
  guaranteed-exhaustive census — a fully exhaustive IDF venue count would
  need a primary source like the CNC's official cinema registry".

That last caveat names the actual fix. Each of the defects above is a
symptom of the same structural gap: **the project has no authoritative
census of what cinemas exist.** Without one, "is this venue missing?" and
"are these two rows the same cinema?" are unanswerable questions, and every
answer has to be reconstructed from the very scraped data whose reliability
is in question.

Crucially, this gap is *only* about reference data. **No official source of
showtimes exists in France** — the CNC publishes establishments and
exploitation visas, never programming. So "prefer official data" cannot be
a project-wide policy; it can only apply to the reference layer, and that
asymmetry is what this ADR makes explicit in the architecture.

## Source recon (verified live, 2026-08-03)

`data.culture.gouv.fr`, the Opendatasoft portal cited in ADR 0024's
follow-up, **no longer serves an API**: it returns `301` to
`culture.data.gouv.fr`, a data.gouv.fr-based portal, and the
`/api/explore/v2.1/` endpoint returns the SPA shell for any dataset path.
Any reference-data work goes through `data.gouv.fr` instead.

| Dataset                                            | Publisher            | Licence            | Format           | Last update           | Content                                                                                                                                                                                                                                                                                                                     |
| -------------------------------------------------- | -------------------- | ------------------ | ---------------- | --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Liste des établissements cinématographiques actifs | CNC                  | Licence Ouverte    | XLSX             | 2026-05-11            | One sheet per year, 2003→2025. Sheet `2025`: **2 065 active establishments**, header on row 5. Columns `NAutoC`, `NomEtab`, `Ecrans`, `fauteuils`, `DEPCOM`, `COMMUNE`, `genre`, `AE`, `3D`                                                                                                                                 |
| Établissements cinématographiques (Île-de-France)  | Région Île-de-France | Licence Ouverte v2 | JSON/GeoJSON/CSV | content vintage ~2022 | **311 IDF establishments**: `ndeg_auto`, `nom`, `adresse`, `code_insee`, `commune`, `dep`, `ecrans`, `fauteuils`, `ae`, `categorie_art_et_essai` (A-E, or `"NON"` for non-Art-et-Essai), `label_art_et_essai` (a concatenated code, e.g. `"RDJP"`, decomposing to RD/JP/PR), `multiplexe`, `genre`, `latitude`, `longitude` |

`NAutoC` and `ndeg_auto` are the same field: the CNC's own establishment
authorization number. It is the stable, officially-assigned, cross-source
identifier the project has been approximating with `slug`,
`paris_cine_info_tid` and `offi_slug`.

The IDF dataset's resource metadata claims a 2017 `last_modified`, but its
payload carries `entrees_2022`/`entrees_2021` columns — the file is
refreshed in place without the metadata following. Treat the metadata date
as unreliable and the payload as roughly a 2022 snapshot.

## Decision

### 1. Introduce a reference layer as a first-class concept

A new package `io/reference/` sits beside `io/scrapers/`, with its own
Protocol (`ReferenceSource`) mirroring `SourceScraper`'s role. The two
layers differ in every operational respect and must not share a contract:

|              | `io/scrapers/` (programme)        | `io/reference/` (referential)                           |
| ------------ | --------------------------------- | ------------------------------------------------------- |
| Answers      | "what is showing, where, when"    | "what exists, and what is it"                           |
| Cadence      | weekly (`scrape`)                 | on demand / annual (`sync-reference`)                   |
| Authority    | none official; best effort        | official by construction                                |
| Writes       | `ScreeningEvent`, `EventSighting` | `Venue`, `Screen`, `Film` reference fields              |
| Failure mode | log and continue, one source down | abort — a corrupt referential is worse than a stale one |

The reference layer never writes screenings, and the programme layer never
creates a venue that the referential could have identified (see
consequences).

### 2. `Venue.cnc_id` becomes the strongest natural key

Resolution order in `VenueRepository.resolve` becomes: `cnc_id` →
per-source external id (`paris_cine_info_tid`, `offi_slug`) → `slug`. The
same lazy-healing mechanism already built for `paris_cine_info_tid` (stamp
an unclaimed row, `merge_venue` into the claiming row otherwise) applies
one level up, so no separate migration pass is needed for existing rows.

New reference-sourced columns on `Venue`: `cnc_id`, `insee_code`,
`screen_count`, `seat_count`, `art_et_essai` (bool), `art_et_essai_grade`
(A-E), `art_et_essai_labels` (list decomposed from RD/JP/PR), `is_multiplex`,
`is_active`.

`screen_count`/`seat_count` on `Venue` are the CNC's own establishment-level
totals and do **not** contradict [ADR 0021](0021-venue-screen-split-and-geocoding.md)'s
`Screen` table: the `Screen` rows remain the per-room detail (names, screen
dimensions, Paris Ciné Info room ids), while the CNC figures are the
official aggregate. A divergence between `Venue.screen_count` and
`COUNT(Screen)` is a useful coverage signal ("we only know 3 of this
multiplex's 15 rooms"), not a conflict to reconcile away.

### 3. Matching is reported, never silently applied below a threshold

Matching 2 065 CNC establishments against ~290 scraped `Venue` rows is a
record-linkage problem, and the project has already been burnt twice by
trusting a name match (`La Grange`, the `needs_review` geocodes). The rule:

- **Automatic** only on a strong triple: normalized name + `insee_code` +
  geographic distance under a fixed threshold from the reference
  coordinates.
- **Everything else lands in a reconciliation report** (a CLI command
  emitting candidates with their scores), resolved by hand via pgweb, the
  same posture as `Venue.needs_review`.
- **Never** a name-only automatic match, at any similarity score.

### 4. Geocoding is re-ranked, not replaced

`GeocodeSource` gains an official tier above the existing two: coordinates
carried by the CNC/IDF referential outrank a BAN address lookup, which
outranks a Photon name search. `io/geocoding.py` is untouched; only the
precedence changes, expressed through the provenance mechanism of
[ADR 0027](0027-field-provenance-and-source-tiers.md).

## Phase 1 implementation notes (2026-08-03)

What actually shipped, and where it deliberately narrows the proposal
above:

- **No new `Venue` rows.** The referential only enriches an existing
  scraped venue (`VenueRepository.apply_official_match`, fill-only-if-null,
  the same safe subset `_apply_venue_detail` already uses) — it never
  creates one from official data alone, mirroring
  `update_venue_passes`/`update_venue_details`'s existing "no screening, no
  row" rule. Surfacing establishments with zero scraped coverage is
  [ADR 0029](0029-showtime-source-expansion.md)'s coverage report, not
  this phase.
- **INSEE matching is a département-level proxy, not a full commune-code
  match, on this first pass.** `Venue` has no `insee_code` before this very
  sync runs, so `io/reference/matching.py` derives a 2-digit département
  from a regex over `Venue.address` and compares it to the establishment's
  `dep` — honestly weaker than the full 5-digit INSEE match the original
  proposal implied, and documented as such in the module's own docstring.
  Once enough venues carry a real `insee_code` from a first sync, a later
  pass could tighten this.
- **Name matching needed one more deterministic normalization step**,
  discovered only once real venue names were compared against the CNC's:
  the CNC/IDF datasets spell establishments without a leading article
  ("GRAND REX") while scraped venue names commonly carry one ("Le Grand
  Rex"). `io/reference/matching.py::_strip_leading_article` strips it
  before comparing — still an exact-equality check, not fuzzy similarity,
  so the "no fuzzy name matching, ever" constraint holds.
- **The reconciliation report is structured-JSONL logging, not a new file
  format.** Every non-automatic candidate is logged at `WARNING`
  (`"reference match needs review"`) with its full check breakdown in
  `ctx`, the same discipline as `pipeline.py`'s `"specialness verdict"`
  logging — `grep`/`jq` over `logs/app.jsonl` is the audit trail. The CLI
  also prints a Rich summary table (`io/console.py::print_reference_sync_summary`).
- **A `--dry-run` flag** on `sync-reference` runs the same matching and
  logging with no database write at all — the harness the manual
  verification pass (below) used, and a way to safely preview a run before
  applying it.
- **Duplicate-target guard**: when two existing venues both pass the full
  automatic triple against the same establishment (an unmerged fragment),
  only the lower-id venue is auto-applied; the other is flagged
  `is_duplicate` and demoted to the manual-review report rather than
  auto-merged — merging is a more invasive action left to a human via the
  existing `merge_venue`.

## Consequences

- **The exhaustiveness question becomes answerable and measurable.** A
  `LEFT JOIN` from the CNC census to the screenings table yields, for the
  first time, the exact list of Île-de-France cinemas with no programme
  coverage — converting "we don't know what we're missing" into a finite,
  ordered backlog. This is the prerequisite for
  [ADR 0029](0029-showtime-source-expansion.md), not an independent nicety.
- **Art & Essai labels are an official specialness prior.** The
  `label_art_et_essai` value `PR` is the state's own designation for
  *Patrimoine et Répertoire* — literally the category of screening this
  project exists to surface — and `RD` (*Recherche et Découverte*) is its
  discovery-oriented sibling. These replace guesswork currently done by the
  heuristic `rare_venue_count` / `sparse_showing_frequency` rules
  ([ADR 0009](0009-specialness-rules-only.md)).
- **The referential is stale by design.** The CNC list is annual; the IDF
  complement is a ~2022 snapshot. A cinema that opened last month is
  absent. So the referential is authoritative about *identity and
  attributes*, never about *currency*: a scraped venue absent from the CNC
  census must be kept and flagged, never deleted. `is_active` records what
  the CNC said, not what Lanterne concludes.
- **Two datasets, two vintages, one entity.** The CNC list is current but
  has no address; the IDF file has addresses and labels but is older. Both
  write to the same `Venue` row, which is precisely why field-level
  provenance ([ADR 0027](0027-field-provenance-and-source-tiers.md)) is a
  hard prerequisite rather than an optional refinement — without it, load
  order silently decides which vintage wins.
- **XLSX parsing is a new dependency** (`openpyxl`) for a source that is
  otherwise ideal. Acceptable: it is a single annual fetch of a
  ~2.6 MB file, parsed offline, with no LLM and no per-run cost.
