# 0011 — Retire MK2 and Le Louxor; keep Le Champo pending its own bug fix

- Status: accepted
- Date: 2026-07-14

## Context

[ADR 0010](0010-keep-mk2-champo-louxor.md) concluded, from
`scripts/compare_source_coverage.py`'s exact-dedup-key spot-check against the
2026-07-14 live scrape, that Paris Ciné Info did not adequately cover MK2 (2/8
matched) or Le Louxor (0/35 matched), and kept all three bespoke scrapers.

That conclusion was challenged and found wrong. Live evidence, fetched
directly from `paris-cine.info`'s authenticated API and homepage:

- Every MK2 room's clean, correctly-cased name is present in the API's own
  showtime data (`MK2 Bastille (Beaumarchais)`, `MK2 Odéon (St Michel)`,
  `MK2 Odéon (St Germain)`, `MK2 Gambetta`, `MK2 Beaubourg`, `MK2 Nation`,
  `MK2 Quai de Seine`, `MK2 Quai de Loire`, `MK2 Bibliothèque`).
- `Le Louxor` and `Le Champo` are both present verbatim, with real showtimes
  (Le Champo confirmed both from the live API and from a Pulp Fiction
  showtime list pasted by the user showing "Le Champo" as a venue alongside
  "Christine Cinema Club").
- Le Louxor's full film list on Paris Ciné Info for the run's date range
  (`12 hommes en colère`, `Dans la chaleur de la nuit`, `Jour de fête`,
  `Les Vacances de Monsieur Hulot`, `Mon oncle`, `Parade`, `Playtime`,
  `Trafic`, `Un Tramway nommé désir`, ...) is essentially the same
  programme the bespoke `louxor.py` scraper found (the Tati 4K
  retrospective, the "Dans la chaleur de la nuit" cycle).

### Why the exact-dedup-key spot-check undercounted

Two compounding measurement problems, not a real coverage gap:

1. **Venue-name fragmentation.** The stored `Venue` table has 16 rows for
   MK2's 11 physical rooms — `normalize_text` (casing/whitespace/apostrophes
   only) does not unify wording variants like `(Beaumarchais)` vs.
   `(côté Beaumarchais)` vs. `(côté Fg St Antoine)`, or `mk2 bastille beaumarchais` (the bespoke scraper's own raw text) vs. `MK2 Bastille (Beaumarchais)` (Paris Ciné Info's). Since `dedup_key` is computed from
   the raw venue string (not the resolved `venue_id`), two sources reporting
   "the same room" in different wording never collide, even though the
   physical venue is identical.
1. **Exact-timestamp/title matching is fragile at scale.** A fuzzy re-check
   (same normalized venue name + same calendar date, ignoring exact time and
   title) found **22 of 43 (51%)** bespoke MK2/Le Louxor screenings had a
   Paris Ciné Info entry at the same venue on the same day — and nearly all
   of the remaining 21 were either already in the past by the time of the
   live re-check (screenings dated 2026-07-06 through 2026-07-13, checked
   live *after* 2026-07-14) or more than three weeks out (a horizon Paris
   Ciné Info's live catalogue likely does not extend to yet), not genuinely
   absent.

## Decision

- **Retire `mk2.py` and `louxor.py`.** Paris Ciné Info's coverage of both is
  confirmed live and directly comparable in programme content, not merely
  "listed in the network" — the original Phase 0 recon's recommendation was
  correct after all.
- **Keep `lechampo.py`, still undecided.** Its 2026-07-14 run produced zero
  usable data (a year-less-date extraction bug rejected both of that week's
  listings — see ADR 0010), so there is nothing to spot-check yet. Revisit
  once that bug is fixed and a real run produces Le Champo data again.
- `mk2.py` and `louxor.py` are deleted (`src/lanterne/io/scrapers/`),
  along with their tests and fixtures
  (`tests/test_mk2.py`, `tests/test_louxor.py`,
  `tests/fixtures/mk2_evenements.html`, `tests/fixtures/louxor_*.html`), and
  removed from `io/scrapers/__init__.py::build_scrapers`.
- `Source.MK2` and `Source.LE_LOUXOR` **stay** in the `Source` enum — the
  `EventSighting` rows already recorded under them are historical data, not
  something a schema change should orphan or need to migrate.

## Consequences

- Paris Ciné Info alone now covers MK2 and Le Louxor going forward; no
  scraper reports their avant-premières/cycles as `curated_source`-special
  anymore. Team presence and cycle framing for these venues now depends
  entirely on Paris Ciné Info's own sparse `com` field (~0.3% of showtimes)
  plus the rule-based specialness classifier (`core/specialness.py`) —
  weaker curated-editorial signal than the retired bespoke scrapers had, but
  the classifier's `institution_venue`/`repertory_rarity`/`rare_venue_count`
  rules still apply to whatever remains genuinely noteworthy.
- **The venue-name fragmentation bug is not fixed by this decision** — it
  only stopped mattering for MK2/Le Louxor because their bespoke scrapers no
  longer run. It still affects every other source (any venue name variant
  across offi.fr, Paris Ciné Info, or a bespoke scraper fragments into
  separate `Venue` rows), which in turn fragments `Venue.accepted_passes`
  enrichment, per-venue stats, and — combined with the same weak
  `normalize_text`, the *film*-title collision found the same day
  ("Vaiana : La Légende du bout du monde" vs "Vaiana, la légende du bout du
  monde") — TMDB enrichment. Both are the same underlying gap (punctuation/
  wording variance `normalize_text` doesn't absorb) and are tracked together
  as an open follow-up in `docs/coverage-matrix.md`, deliberately not fixed
  in this pass: it touches the core title/venue matching logic every
  screening's dedup depends on and needs its own design pass, not a rushed
  patch riding on a retirement decision.
