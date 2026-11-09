# 0025 — Venue address research completion and the LLM venue-paraphrasing bug

- Status: accepted
- Date: 2026-07-17

## Context

[ADR 0024](0024-venue-data-quality-cleanup.md) cleared ~90 venues' worth of
contaminated or missing addresses and deferred filling them to a manual
research pass. This ADR covers that pass — completed with Playwright
(browser automation) plus web search — and a second, distinct scraper bug
it surfaced along the way: unlike ADR 0024's geocoding-pipeline bug, this
one lives in the scrapers themselves and was still live at the time of
writing.

## Address research

Every address was read directly off the venue's own page on offi.fr — the
same site `io/scrapers/offi.py` already scrapes for screenings — browsed
department by department (`/cinema/<department-slug>.html`) across all
seven Île-de-France departments the project covers, falling back to web
search for venues outside offi.fr's own department listings (chiefly
Paris-based independents, which offi.fr does not list by department at
all — confirmed via a direct 404 on `/cinema/paris.html`). Applied via
`scripts/fill_venue_details_independents_1.py` through `_4.py`.

Several existing addresses turned out to be silently wrong rather than
missing — the same failure shape as ADR 0024, still slipping past its
Île-de-France-postal-code contamination filter because the wrong code was
still a real IDF one (`UGC Le Majestic`, `Pathé Levallois`, `Pathé Quai d'Ivry`, `CGR Épinay-sur-Seine`, `CGR Sarcelles`, `Megarama Cormeilles-en-Parisis`, `Cinéma Alice Guy`, `Le Vincennes`, `Luxy`, `Le Cube Garges`, `Le Figuier Blanc`), plus three cases of the ADR
0024-documented geocoding-contamination pattern surviving specifically
because they were never flagged by that ADR's postal-code filter in the
first place (`Apollo`, `Conti`, `La Rotonde (Sénart)` — all still carried
an unrelated Paris/Versailles address from a `NAME_SEARCH` guess).

### Disambiguating generic venue names

Several DB venues share a name with more than one distinct offi.fr
listing (`Trianon`, `Cyrano`, `Jacques-Prévert`, `Cinoches Desnos`,
`Apollo` — French municipal cinemas draw heavily on a small pool of
names). Guessing wrong here is exactly the kind of silent corruption ADR
0024 spent most of its effort undoing, so each was resolved by
cross-referencing the DB venue's own currently-scheduled films against
offi.fr's per-venue listings for every same-named candidate, keeping only
an exact-film match as confirmation:

- `Jacques-Prévert` (id 177) → Les Ulis, not Gonesse (shared screenings of
  "Shana" and "Ulysse"; Gonesse has no corresponding DB row at all).
- `Cinoches Desnos` (id 151) → offi.fr's "Cinoches RN7" in Ris-Orangis
  (shared screenings of "La Chaleur" and "Les Caprices de l'enfant Roi"),
  despite the display-name mismatch.
- `Trianon` (id 256) → Sceaux, not Romainville (shared screening of "Le
  Vertige").
- `Apollo` (id 108) → Pontault-Combault, not the Versailles address it
  held (shared screenings of "Backrooms", "Des Minions et des monstres",
  "Supergirl", "Toy Story 5").
- `Cyrano` (id 157) → confirmed as Montgeron via shared screening of "In
  Waves", a **different** venue from `UGC Cyrano` (id 158, Versailles) —
  a merge of the two had been provisionally planned before this check and
  was **not** done; `Cyrano`'s Versailles-looking address was itself
  contamination (a `NAME_SEARCH` guess landing near `UGC Cyrano`), now
  corrected to Montgeron instead of merged away.

## The LLM venue-paraphrasing bug

While resolving the `Cinémathèque Pathé` vs `Fondation Jérôme Seydoux-Pathé` pair (already merged once in ADR 0024's
`fix_venue_address_collision_duplicates.py`), the loser row was found to
hold **future** screenings (September/October 2026) — proof the
fragmentation was still actively recurring, not a historical leftover.

Root cause: `io/scrapers/fondationpathe.py` (and three sibling
single-venue text scrapers — `cinematheque.py`, `forumdesimages.py`,
`lavillette.py`) each embed their own hardcoded venue name as the first
line of the raw text handed to the LLM extractor, but never asserted it
on the structured result — the LLM was free to paraphrase it, and
sometimes did (`"Fondation Jérôme Seydoux-Pathé"` → `"Cinémathèque Pathé"`). This is the same shape of bug `structure_via_llm`'s
`known_starts_at` parameter already exists to prevent for dates (see
[ADR 0012](0012-retire-lechampo.md)), just never generalized to the venue
field.

**Code fix**: `structure_via_llm` (`io/scrapers/base.py`) gained a
`known_venue` parameter, applied the same way as `known_starts_at` —
overriding the LLM's guess before plausibility validation runs. All four
single-venue scrapers now pass their `_VENUE` constant through it.
Regression tests added per scraper (`test_fetch_events_known_venue_wins_ over_a_paraphrased_llm_guess` in each of `test_fondationpathe.py`,
`test_forumdesimages.py`, `test_lavillette.py`, `test_scrapers.py`) plus
one direct unit test on `structure_via_llm` itself
(`test_structure_via_llm_known_venue_overrides_a_paraphrased_guess`).

**Data fix**: `scripts/fix_venue_scraper_venue_name_duplicates.py` merges
the two pairs found (`Studio 28` ← `Cinéma Studio 28`, an address-format
variant unrelated to the LLM bug; `Fondation Jérôme Seydoux-Pathé` ←
`Cinémathèque Pathé`).

## Net effect

|                               | Before this ADR | After                                                                                  |
| ----------------------------- | --------------- | -------------------------------------------------------------------------------------- |
| Venue rows                    | 271             | 269                                                                                    |
| Venues with an address        | 245             | 264                                                                                    |
| Venues with coordinates       | —               | 266                                                                                    |
| Venues flagged `needs_review` | —               | 10 (all `NAME_SEARCH`-tier, correctly flagged as lower-confidence, not further chased) |

Five venues remain without an address: `Ciné-bar Hôtel de Ville`,
`Ciné-club Les Icônes de la Comédie Musicale`, `Les Studios`, `Lost in Frenchlation (english subs)`, `Ramy Ciné-club` — one-off event spaces
(bars, curated ciné-clubs) with 1–2 screenings each and no matching
offi.fr listing, genuinely outside what a cinema directory covers rather
than a research gap.

## Consequences

- The `known_venue` pattern is now the standard way for a single-venue
  text scraper to bind a deterministic fact past the LLM — any future
  single-venue scraper should use it from the start rather than relying
  on the raw-text hint alone.
- The address-enrichment scripts are historical record, not meant to be
  re-run, matching the convention set by every prior `fix_*.py`/
  `fill_venue_details_*.py` script.
- Per the original request that scoped this work: venue data is treated
  as effectively static and is not re-scraped on a schedule — screenings
  remain the only side that refreshes continuously.
