# 0010 — Keep MK2, Le Champo, and Le Louxor as bespoke scrapers

- Status: **superseded by [ADR 0011](0011-retire-mk2-louxor.md)**
- Date: 2026-07-14

> **Superseded, same day.** The exact-dedup-key spot-check below undercounted
> real coverage: `compare_source_coverage.py` requires an identical
> title/venue/timestamp match, which false-negatives on venue-name
> fragmentation (multiple `Venue` rows for the same physical MK2 room, e.g.
> "MK2 Bastille (Beaumarchais)" vs "MK2 Bastille (côté Beaumarchais)" vs
> "mk2 bastille beaumarchais") and on exact-minute timestamp drift between
> sources. A follow-up fuzzy check (same venue + same calendar date, no exact
> title/time match required) against live Paris Ciné Info data found MK2 and
> Le Louxor genuinely well covered — see ADR 0011 for the corrected evidence
> and decision. Kept here, unedited, as the historical record of the mistake
> and how it was caught (the user pointed at Paris Ciné Info's own live pages
> showing Le Champo, Le Louxor, and every MK2 room present with real
> showtimes, which is what triggered the re-investigation).

## Context

`docs/coverage-matrix.md`'s Phase 0 recon (2026-07-13, a single reconnaissance
pass over Paris Ciné Info's catalogue) found all three venues present in the
network and tentatively recommended **retiring** `mk2.py`, `lechampo.py`, and
`louxor.py` once confirmed redundant with the newly-extended Paris Ciné Info
scraper — pending a live spot-check with
[`scripts/compare_source_coverage.py`](../../scripts/compare_source_coverage.py),
which needs a real `scrape` run with Paris Ciné Info credentials configured to
mean anything.

That real run happened on 2026-07-14 (see the all-screenings expansion
finally landing production data: 40,109 stored screenings, 39,690 of them
ordinary). The spot-check was then run for real against it.

## Decision

**Keep all three bespoke scrapers — do not retire any of them.** The live
spot-check contradicts the recon-pass assumption:

| Source                | Screenings this run | Also on Paris Ciné Info | At risk if retired       |
| --------------------- | ------------------- | ----------------------- | ------------------------ |
| `mk2.com`             | 8                   | 2                       | **6 (75%)**              |
| `cinemalouxor.fr`     | 35                  | 0                       | **35 (100%)**            |
| `cinema-lechampo.com` | 0                   | 0                       | inconclusive — see below |

- **MK2**: three-quarters of what the bespoke scraper reports this week
  (including avant-premières with confirmed team presence) has no matching
  Paris Ciné Info sighting at all. The venue being *listed* in Paris Ciné
  Info's network (the Phase 0 recon's finding) does not mean every one of
  its screenings is actually populated there week to week — a directory
  listing is not the same guarantee as full showtime coverage.
- **Le Louxor**: zero overlap. None of its 35 screenings this run (a Rohmer
  retrospective, a "Dans la chaleur de la nuit" cycle, a Tati 4K
  retrospective, a Wives trilogy ciné-club) appear on Paris Ciné Info at all
  in this run, despite the recon pass listing it as present in the network.
- **Le Champo**: the bespoke scraper reported zero events this run, but for
  an unrelated reason — both of the week's ciné-club listings had their
  LLM-extracted date resolved to a moment already in the past
  (`starts_at is in the past`), so the plausibility guard
  (`core/validation.py`) correctly rejected them before ingestion. This is
  very likely a **year-less-date resolution bug** in `lechampo.py`'s LLM
  extraction (see the "Open follow-up" below), not evidence that Le Champo
  has nothing to report — a zero-signal result here cannot support a
  retirement decision either way.

## Consequences

- `mk2.py`, `lechampo.py`, and `louxor.py` stay registered in
  `io/scrapers/__init__.py::build_scrapers` — no scraper files removed.
- `docs/coverage-matrix.md`'s recommendation column for these three sources
  is corrected from "Retire" to reflect this decision and the real numbers.
- The Phase 0 recon's "✅ yes, in the network" signal is not, by itself, a
  reliable predictor of full showtime coverage — a venue's presence in Paris
  Ciné Info's cinema directory does not guarantee its full programme is
  populated there. Any future retirement candidate must be re-verified the
  same way (a real `compare_source_coverage.py` run against a live scrape),
  not inferred from the directory listing alone.
- Open follow-up, not addressed by this decision: `lechampo.py`'s year-less
  date resolution appears to be producing past dates for at least some
  listings — worth its own investigation (likely a reference-date
  off-by-one or a French date phrase the LLM misreads), separate from the
  retirement question this ADR settles.
