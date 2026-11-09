# 0029 — Showtime source expansion: coverage-driven, robots.txt-gated

- Status: proposed
- Date: 2026-08-03

## Context

Showtime coverage has so far been expanded opportunistically: a source was
evaluated, implemented, then retired when another turned out to cover it
([ADR 0011](0011-retire-mk2-louxor.md),
[ADR 0012](0012-retire-lechampo.md)). That worked while the question was
"does source B already cover source A", which is answerable by comparing
two scrapers' outputs. It does not answer the question that actually
matters — **which Île-de-France cinemas have no coverage at all** — because
the only census available was itself a scraped one, with the caveat
recorded in [`docs/coverage-matrix.md`](../coverage-matrix.md) that
offi.fr's directory "is the best single source checked so far, not a
guaranteed-exhaustive census".

Two further decisions were left implicit and are settled here.

**Posture on undocumented internal APIs.** Pathé was dropped on a bare 403
([`docs/scraping-strategy.md`](../scraping-strategy.md)) and UGC deferred
pending "a real browser's network tab" to find its XHR endpoint. Neither
had a stated policy behind it, so each new chain re-opened the question.

**What counts as evidence to retire or skip a source.** ADRs 0010→0012
show the cost of getting this wrong: ADR 0010 concluded "keep all three"
from an exact-dedup-key comparison that undercounted real coverage, and was
reversed two days later by ADRs 0011 and 0012.

## Decision

### 1. Coverage gaps are computed from the official census, not guessed

Once [ADR 0026](0026-official-reference-data-layer.md)'s CNC referential is
loaded, a `coverage-report` CLI command joins the census against stored
screenings and emits, per Île-de-France establishment: CNC id, name,
commune, screen count, Art & Essai label, and the count of screenings seen
in the last N days. Establishments at zero are the backlog, ordered by
screen count (a 15-screen multiplex missing is a bigger hole than a
single-screen village cinema).

**No new showtime source is implemented before this report exists.** The
report is what makes "add UGC" an evidence-backed decision rather than an
intuition, and it is cheap once the referential is in place.

### 2. Undocumented internal APIs are permitted, gated on robots.txt

An internal JSON endpoint discovered through a site's own front end is a
legitimate Level 2 source under
[`docs/scraping-strategy.md`](../scraping-strategy.md), subject to all of:

1. **`robots.txt` does not disallow the path** for a generic user-agent.
   Checked at implementation and recorded in the scraper's module
   docstring with the date it was checked.
1. **No authentication is circumvented.** An account-gated API is used only
   with an account the project owner legitimately holds and has authorized
   — the standing from [ADR 0007](0007-paris-cine-info.md), unchanged.
1. **No anti-bot measure is worked around.** A 403, a CAPTCHA, or a
   JS-challenge is a refusal, and refusals are respected. This keeps
   `pathe.fr` out regardless of what its API looks like — its front end was
   confirmed live on 2026-08-03 to be serving a maintenance/failover page
   behind Akamai, which is not an invitation to probe further.
1. **Low, identifiable volume**: one pass per weekly scrape, identifiable
   User-Agent — the existing "be a good citizen" rule, unchanged.
1. **A per-source ADR** records the reasoning, as ADR 0007 did.

Verified live on 2026-08-03 for the immediate candidates: `allocine.fr`'s
`robots.txt` disallows `/rechercher/` and `/salle/recherche/` for the
generic user-agent — consistent with what `docs/scraping-strategy.md`
already noted about cinema-id discovery — while `www.pathe.fr` was serving
a failover page and `ugc.fr`'s `robots.txt` resolved only through
redirects. Both need re-checking at implementation time rather than being
treated as settled here.

### 3. A source is retired only on positive evidence, per venue

The ADR 0010 reversal came from comparing exact dedup keys, where
venue-name fragmentation and exact-minute timestamp mismatches produced
false negatives. The standing rule:

- Overlap is measured **fuzzily** (venue + date, not exact timestamp), and
  after [ADR 0026](0026-official-reference-data-layer.md) on `cnc_id` —
  which removes venue-name fragmentation as a confounder entirely, the
  actual root cause of ADR 0010's error.
- Retirement evidence is **per venue**, never per source in aggregate.
- The comparison is **run against the live database**, never reasoned from
  the scrapers' code.

### 4. Expansion order

Strictly by the cost hierarchy of
[`docs/scraping-strategy.md`](../scraping-strategy.md), applied to whatever
the coverage report actually shows is missing — the order below is the
current expectation, and the report overrides it if reality disagrees:

1. **Chain internal APIs** (UGC, and any other chain whose front end
   reveals a JSON endpoint) — Level 2, no LLM, one endpoint covering many
   venues at once. The highest coverage-per-unit-of-effort available, and
   the reason the gate in §2 was worth settling.
1. **OpenAgenda / municipal open-data feeds** for the institutional and
   municipal venues that run their own programming outside any commercial
   aggregator — the category
   [`docs/coverage-matrix.md`](../coverage-matrix.md) confirms is absent
   from both Paris Ciné Info and offi.fr. Level 1–2, official-adjacent,
   unverified as of this ADR and to be confirmed venue by venue against the
   coverage report.
1. **Allociné per-cinema pages** — Level 4, no team-presence signal, and
   needs a venue → Allociné-id mapping. Cheaper to build after
   [ADR 0026](0026-official-reference-data-layer.md) than before, since the
   CNC census supplies the definitive venue list to map from.
1. **Bespoke venue scrapers** — the current default, now explicitly the
   last resort rather than the first move.

## Consequences

- **A deliberate pause.** No new showtime source ships until the coverage
  report exists. Given that the last three source decisions produced two
  retirements and one reversal, spending that time on the measurement is
  the cheaper path — but it is a real delay to coverage growth and is
  called out as such rather than presented as free.
- **Chain APIs raise the stakes on breakage.** One endpoint covering
  dozens of venues is efficient, and it is also a single point of failure
  whose silent breakage removes a large share of coverage at once. The
  existing "fail loudly in tests, softly in prod" rule needs a companion:
  the coverage report should be compared run over run, so a source going
  quiet is visible rather than merely logged.
- **The robots.txt gate is a snapshot.** A site can tighten its
  `robots.txt` after a scraper ships. Re-checking is not automated by this
  decision; the check date recorded in each module docstring is what makes
  a stale one findable.
- **Retiring a source now requires the referential.** §3's per-venue,
  `cnc_id`-based comparison cannot run before
  [ADR 0026](0026-official-reference-data-layer.md) lands. Until then, no
  further retirements.
