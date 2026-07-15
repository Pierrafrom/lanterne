# 0012 — Retire Le Champo

- Status: accepted
- Date: 2026-07-14

## Context

[ADR 0011](0011-retire-mk2-louxor.md) retired `mk2.py` and `louxor.py` once
live evidence confirmed Paris Ciné Info genuinely covers both venues'
programmes, but left `lechampo.py` in an explicit "keep, undecided" state:
the 2026-07-14 run's spot-check was inconclusive, because `lechampo.py`
itself produced zero usable data that week — both of its ciné-club listings
had their LLM-extracted date resolved to a moment already in the past
(`starts_at is in the past`, rejected by `core/validation.py`'s plausibility
guard). This is very likely a year-less-date resolution bug: `lechampo.py`
embeds a `Reference date: ...` line in the LLM prompt and asks the model
itself to resolve a day+month pin (e.g. "📍 25 juin") into "the next upcoming
occurrence" (`io/llm.py:47-48`) — arithmetic a small local model handles
unreliably in free generation.

Rather than debugging or hand-rolling deterministic date resolution for a
scraper whose entire justification (a bespoke source Paris Ciné Info might
not cover) might not hold, the question was: does Paris Ciné Info already
report Le Champo's programme directly, the same way it turned out to for MK2
and Le Louxor?

## Evidence

Confirmed live (2026-07-14), same authenticated API used for ADR 0011's
corrected spot-check: querying every current film's showtimes and filtering
`title == "Le Champo"` returns **89 real, dated showtimes** from today
through 2026-07-21 — a full repertory programme (Jacques Tati's `Parade`,
`Les Vacances de Monsieur Hulot`; also `Mulholland Drive`, `Riz amer`,
`Sans pitié`, `Pâques sanglantes`, `Les Choses de la vie`, ...), all with
empty `com` fields — consistent with the coverage matrix's finding that only
~0.3% of Paris Ciné Info showtimes carry a comment at all. This is the same
shape of evidence (a real, current, comparable programme — not merely
"listed in the network") that justified retiring MK2 and Le Louxor.

## Decision

**Retire `lechampo.py`.** The date-resolution bug becomes moot rather than
fixed — there is no code left to have the bug.

Same accepted trade-off as ADR 0011: Le Champo's curated `cycle_name` /
ciné-club framing is lost (Paris Ciné Info's `com` field is far too sparse to
replace it), but `core/specialness.py`'s `rare_venue_count` and
`sparse_showing_frequency` rules already catch exactly this screening's
profile — a single-venue, low-frequency ciné-club slot — the same safety net
ADR 0011 already relied on for MK2 and Le Louxor.

- `lechampo.py` is deleted (`src/lanterne/io/scrapers/`), along with
  its test and fixture (`tests/test_lechampo.py`,
  `tests/fixtures/lechampo_cineclubs.html`), and removed from
  `io/scrapers/__init__.py::build_scrapers`.
- `Source.LE_CHAMPO` **stays** in the `Source` enum — its historical
  `EventSighting` rows are not orphaned by this change, same treatment as
  `Source.MK2`/`Source.LE_LOUXOR`.

## Consequences

- Paris Ciné Info now covers Le Champo going forward; team presence and
  cycle framing depend on the same weaker signal already accepted for MK2
  and Le Louxor (ADR 0011's "Consequences" section).
- The year-less-date-resolution pattern this bug exposed (`Reference date:`
  embedded in the prompt, LLM resolves "next occurrence" itself) is not
  unique to the now-deleted `lechampo.py` — `forumdesimages.py` and
  `lavillette.py` use the identical pattern (`io/scrapers/forumdesimages.py`,
  `io/scrapers/lavillette.py`) and have not been audited for the same
  failure mode. Not addressed by this decision — flagged as a follow-up,
  since both remain active bespoke scrapers with no Paris Ciné Info
  equivalent to fall back on.
