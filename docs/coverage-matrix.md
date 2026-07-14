# Venue coverage matrix — Phase 0 (all-screenings expansion)

Built while evaluating the shift from "special screenings only" to "every
screening, with specialness detected after ingestion" (see
[ADR 0008](decisions/0008-drop-allocine-width-source.md) for why AlloCiné
was ruled out as the width source, and [ADR 0007](decisions/0007-paris-cine-info.md)
for Paris Ciné Info's original scope).

## Method

Paris Ciné Info's `get_movies.php` (without the `events=true` filter) and
`get_showtimes.php` were walked once, in full, on 2026-07-13 (a single
reconnaissance pass, in the spirit of ADR 0007's "one pass per weekly
scrape" posture — not a recurring probe). Results:

- **494 films** in the full catalogue (vs. 27 under the current
  `events=true` filter — the filter the live scraper applies today).
- **15,491 showtimes** across **78 distinct venues**.
- Only **42 showtimes** (0.27%) carry a non-empty `com` field — confirms
  that field alone is far too sparse to be the primary specialness signal
  once the source covers everything; useful as one input, not sufficient
  on its own (see the rule-based specialness classifier, Phase 3,
  [`core/specialness.py`](../src/cine_event_bot/core/specialness.py) and
  [ADR 0009](decisions/0009-specialness-rules-only.md)).

## Current bespoke scrapers vs. the Paris Ciné Info network

| Source (`src/cine_event_bot/io/scrapers/`) | Venue(s)                                 | On Paris Ciné Info?                                                                                                                    | Showtimes seen | Recommendation                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------------------------ | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `mk2.py` (removed)                         | MK2 (11 Paris locations)                 | ✅ yes — all 11 rooms present (Bibliothèque, Beaubourg, Nation, Quai de Seine, Gambetta, Quai de Loire, Odéon×2, Bastille×2, Parnasse) | 2,479 combined | **Retired** (2026-07-14, [ADR 0011](decisions/0011-retire-mk2-louxor.md)) — a first exact-dedup-key spot-check wrongly found low overlap (venue-name fragmentation + exact-timestamp matching false-negatived); a corrected fuzzy (venue+date) check confirmed real coverage.                                                                                                                          |
| `lechampo.py` (removed)                    | Le Champo                                | ✅ yes                                                                                                                                 | 100            | **Retired** (2026-07-14, [ADR 0012](decisions/0012-retire-lechampo.md)) — the 2026-07-14 spot-check was inconclusive (a year-less-date extraction bug rejected both of that week's listings), but a direct live query confirmed Paris Ciné Info reports 89 current Le Champo showtimes; retired on the same evidence standard as MK2/Le Louxor rather than fixing a bug in a scraper no longer needed. |
| `louxor.py` (removed)                      | Le Louxor                                | ✅ yes                                                                                                                                 | 108            | **Retired** (2026-07-14, [ADR 0011](decisions/0011-retire-mk2-louxor.md)) — same correction as MK2; Le Louxor's Paris Ciné Info programme (Tati retrospective, "Dans la chaleur de la nuit" cycle) matched the bespoke scraper's own findings almost exactly once compared correctly.                                                                                                                  |
| `premiereprojo.py`                         | n/a (aggregator, not a venue)            | n/a                                                                                                                                    | n/a            | **Keep** — curated avant-première signal (`avpType == AVPE` for team presence) that Paris Ciné Info's sparse `com` field doesn't reliably replace.                                                                                                                                                                                                                                                     |
| `paris_cine_info.py`                       | n/a (aggregator, 78 venues)              | —                                                                                                                                      | —              | **Extended** (Phase 2) — full catalogue, no `events=true` filter; a blank-`com` showtime is stored directly as an ordinary screening. Primary width source for Paris intra-muros.                                                                                                                                                                                                                      |
| `offi.py`                                  | n/a (aggregator, ~115 IDF suburb venues) | —                                                                                                                                      | —              | **New** (Phase 2) — Level 4, no LLM (fully tabular DOM). Primary width source for the IDF suburbs, complementing Paris Ciné Info's Paris-only coverage.                                                                                                                                                                                                                                                |
| `cinematheque.py`                          | La Cinémathèque française                | ❌ no                                                                                                                                  | 0              | **Keep bespoke** — only source.                                                                                                                                                                                                                                                                                                                                                                        |
| `forumdesimages.py`                        | Le Forum des images                      | ❌ no                                                                                                                                  | 0              | **Keep bespoke** — only source.                                                                                                                                                                                                                                                                                                                                                                        |
| `fondationpathe.py`                        | Fondation Jérôme Seydoux-Pathé           | ❌ no                                                                                                                                  | 0              | **Keep bespoke** — only source.                                                                                                                                                                                                                                                                                                                                                                        |
| `lavillette.py`                            | Cinéma en plein air de La Villette       | ❌ no                                                                                                                                  | 0              | **Keep bespoke** — only source (a seasonal open-air programme, structurally different from a regular cinema anyway).                                                                                                                                                                                                                                                                                   |

## Full venue list seen on Paris Ciné Info (78)

Chains: UGC (Ciné Cité Les Halles, Ciné Cité Bercy, Ciné Cité Paris 19,
Maillot, Lyon Bastille, Odéon, Gobelins, Danton, Opéra, Montparnasse,
Rotonde), Pathé (La Villette, Parnasse, Beaugrenelle, Aquaboulevard,
Convention, Alésia, Palace, BNP Paribas, Montparnos, Les Fauvettes,
Wepler), MK2 (11 rooms, listed above), CGR Paris Lilas.

Independents: Les 7 Parnassiens, 7 Batignolles, Le Grand Rex, Les 5
Caumartin, Le Brady, Le Cinéma des Cinéastes, Lucernaire, L'Arlequin,
Saint-André des Arts, Les 3 Luxembourg, L'Entrepôt, Le Balzac, Majestic
Passy, Chaplin Saint Lambert, Elysées Lincoln, Espace Saint-Michel,
L'Archipel, La Géode, Le Grand Action, L'Épée de Bois, Filmothèque du
Quartier Latin, Ecoles Cinema Club, Christine Cinema Club, Publicis
Cinémas, Reflet Medicis, Nouvel Odéon, Escurial, Le Saint Germain des
Prés, Majestic Bastille, Studio des Ursulines, Max Linder Panorama,
Cinéma du Panthéon, Luminor Hôtel de Ville, Chaplin Denfert, Jeu de
Paume, Studio 28, Mac-Mahon, Studio Galande, Le CiNey, Club de l'étoile,
La Clef, Maison de la Culture du Japon à Paris.

## What Paris Ciné Info does not cover

The four patrimonial/institutional sources — confirmed absent from the
494-film catalogue:

- La Cinémathèque française
- Le Forum des images
- Fondation Jérôme Seydoux-Pathé
- La Villette (Cinéma en plein air) — seasonal open-air programme

These run their own programming/ticketing outside any commercial
aggregator and are expected to also be absent from offi.fr (not yet
confirmed — see Open questions).

## Offi.fr — the Île-de-France suburb complement

Confirmed live (2026-07-13): offi.fr has one listing page per IDF
department, distinct from the Paris-only Paris Ciné Info network:

- `offi.fr/cinema/seine-saint-denis.html` (93) — **25 cinémas**, real
  addresses/films/genres confirmed (e.g. Ciné 104 Pantin, L'Écran de
  Saint-Denis, Espace 1789 Saint-Ouen, Studio Aubervilliers).
- Six more department pages exist on the same pattern: `hauts-de-seine.html`
  (92), `val-de-marne.html` (94, expected), `seine-et-marne.html` (77,
  expected), `yvelines.html` (78, expected), `essonne.html` (91,
  expected), `val-d-oise.html` (95, expected) — only 92 and 93 fetched so
  far; the other five follow the same confirmed URL pattern and are
  expected to hold at zero incremental integration risk.
- `robots.txt` places no restriction on any `/cinema/` path.

At ~15–25 venues per department × 7 departments, offi.fr adds on the
order of **100+ suburb venues** that the Paris Ciné Info network (78,
intra-muros only) does not reach — a substantial, distinct complement,
not overlapping coverage. This resolves the open geographic-reach
question: **offi.fr is the IDF suburb pillar, Paris Ciné Info is the
Paris intra-muros pillar** — both needed for the stated Paris/IDF scope.

Structure, confirmed live on 2026-07-13 against a real department page
(Seine-Saint-Denis) and a real venue page (Ciné 104, Pantin): the
department page groups venues under a `data-idlieu`-tagged block, each
linking to `/cinema/<venue-slug>.html`, paginated via a `ul.pagination`
nav (1 to 3 pages depending on the department). Each venue's own page
shows a templated date-tab grid — no embedded JSON (`application/ld+json`
present but only site-wide organization metadata) — confirming Level 4 in
`scraping-strategy.md`'s taxonomy. The grid turned out to be tabular
enough (`itemscope itemtype="schema.org/Movie"` tiles, `HH:MM` badges) to
map **without an LLM**, a deliberate deviation from the "Level 4 → LLM"
default (see `scraping-strategy.md`'s cross-cutting rules). The date is
never printed with a year (only "Lundi 13 Juillet"); the page's eight day
tabs (`#t_0`..`#t_7`) were confirmed to always be sequential days starting
from the fetch date, so the date is computed by arithmetic rather than
parsed from that text. Implemented — see
[`offi.py`](../src/cine_event_bot/io/scrapers/offi.py).

## Remaining open items

Resolved during Phase 2 implementation (2026-07-13):

1. ~~Confirm the five untested offi.fr department pages~~ — all five (77
   `seine-et-marne`, 78 `yvelines`, 91 `essonnne` — note offi.fr's own
   typo in that slug, confirmed live, `essonne.html` 404s — 94
   `val-de-marne`, 95 `val-d-oise`) resolve on the same pattern.
1. ~~Offi.fr real-markup verification~~ — confirmed Level 4, no embedded
   JSON, and (see above) mappable without an LLM.

Resolved during Phase 3 implementation (2026-07-13):

1. ~~Specialness classification pipeline~~ — implemented as
   [`core/specialness.py`](../src/cine_event_bot/core/specialness.py), a
   rule-based cascade (team presence, cycle name, institution venue, film
   age/rarity) run in `IngestionPipeline` right after film enrichment. No
   LLM fallback — see [ADR 0009](decisions/0009-specialness-rules-only.md)
   for why the originally planned LLM-fallback branch has no real caller
   given what the two width sources actually scrape today.

Resolved (Phase 4, 2026-07-13):

1. ~~14-day retention/pruning for ordinary screenings~~ — implemented as
   `EventRepository.prune_ordinary_screenings` and the `prune-db` CLI
   command; deletes `is_special=False` screenings whose `starts_at` is more
   than 14 days in the past (and their `EventSighting` rows), never a
   special one. Schedule it after `scrape` in the weekly cron (see
   `docs/setup.md`).

Resolved (real `scrape` run, 2026-07-14 — 40,109 screenings stored, 39,690 of
them ordinary, the all-screenings expansion's first real data):

1. ~~MK2/Champo/Louxor retirement spot-check~~ — run for real against the
   live database. A first pass ([ADR 0010](decisions/0010-keep-mk2-champo-louxor.md))
   wrongly concluded "keep all three" from an exact-dedup-key comparison
   that undercounted real coverage (venue-name fragmentation, exact-minute
   timestamp mismatches). A corrected fuzzy check confirmed real coverage
   for MK2 and Le Louxor — **both retired**; Le Champo stays undecided
   pending its own bug fix. See
   [ADR 0011](decisions/0011-retire-mk2-louxor.md) for the corrected
   evidence and final decision.
1. ~~Cross-source dedup at scale~~ — confirmed healthy at real volume: 48
   screenings this run were reported by more than one source and correctly
   merged into one `ScreeningEvent` row each (40,159 `EventSighting` rows
   across 40,109 events), no collision errors.

Resolved (2026-07-14, follow-up pass — see
[ADR 0012](decisions/0012-retire-lechampo.md)):

1. ~~Le Champo year-less-date extraction.~~ Rather than debugging the LLM's
   date resolution, a direct live query confirmed Paris Ciné Info already
   reports Le Champo's full programme (89 current showtimes) — `lechampo.py`
   is retired, same evidence standard as ADR 0011, making the date bug moot.
1. ~~Venue-name fragmentation (the `Venue`-side half of the `normalize_text`
   gap below).~~ `Venue.paris_cine_info_tid` (Paris Ciné Info's own stable
   theatre id, carried on every `Sighting` as `venue_external_id`) is now a
   stronger natural key than the text-based `slug`, checked first in
   `EventRepository._resolve_venue`. A pre-existing fragmented row is healed
   lazily — stamped with its tid if unclaimed, or merged into the
   tid-owning row via the new `merge_venue` (mirrors `merge_film`) if
   another row already claims it — the next time any of that venue's
   showtimes is (re-)ingested, no separate cleanup pass needed. Only
   Paris Ciné Info populates `venue_external_id` today; every other source's
   venue resolution is unchanged.

Resolved (2026-07-14, robustness pass):

1. ~~The year-less reference-date-resolution pattern that caused the Le
   Champo bug was not unique to it.~~ `forumdesimages.py` and
   `lavillette.py` used the identical pattern (embed `Reference date: ...`
   in the LLM prompt, ask the model to resolve "next occurrence" itself).
   Both now resolve the date deterministically in Python
   (`core/frenchdate.py::resolve_next_occurrence`) and correct the LLM's
   output with it via `structure_via_llm`'s new `known_starts_at` param —
   the LLM is still used to classify `event_type`/`cycle_name`/
   `has_team_present`, but its own date arithmetic is never trusted, the
   same "known-true facts win" posture already proven in
   `paris_cine_info.py`.

Still open:

1. **`normalize_text` too weak for real-world wording variance — the film
   -title half only.** Confirmed live (2026-07-14): "Vaiana : La Légende du
   bout du monde" and "Vaiana, la légende du bout du monde" become two
   different `Film` rows that both resolve to the same TMDB film — the
   second one to save collides on `Film.tmdb_id`'s unique constraint (a real
   `IntegrityError`, now logged and skipped rather than crashing the run,
   but the losing `Film` row never gets TMDB metadata). One title pair alone
   accounted for 4,183 of 4,455 such collisions in this run. Fixed via
   `find_film_by_tmdb_id`/`merge_film` (see the Fixed section of
   `CHANGELOG.md`) — `core/dedup.py::normalize_text` itself is still
   unchanged (casing/whitespace/apostrophes only, not punctuation), the fix
   is a merge-after-the-fact, same shape as the venue-side fix above.
1. **Specialness classifier tuning against real data.** Six rules now (see
   [ADR 0009](decisions/0009-specialness-rules-only.md)'s 2026-07-13
   update — two more added, `rare_venue_count`/`sparse_showing_frequency`,
   plus a batch-pass architecture fix), and an evaluation harness exists
   (`uv run cine-event-bot eval-specialness`, see `docs/testing.md`) scoring
   accuracy/precision/recall against
   [`eval/golden_specialness.json`](../eval/golden_specialness.json). The
   hand-built golden set is still small — but every classified screening's
   full feature vector and verdict is now logged as structured JSONL
   (`"msg":"specialness verdict"`, both fired and not-fired, see
   `pipeline.py::_log_specialness_verdict`), so `grep '"msg":"specialness verdict"' logs/app.jsonl` accumulates a real labeled dataset from
   production runs to sample and re-tune the thresholds from, without
   needing a bespoke labeling pass.
