# 0024 — Venue data-quality cleanup: corrupted historical rows and self-inflicted geocoding contamination

- Status: accepted
- Date: 2026-07-17

## Context

A full audit of the `venue` table (289 rows at the start), prompted by a
request to fill every missing field by hand, surfaced far more than
missing data: several classes of *wrong* data, some pre-dating this
session's work, one entirely self-inflicted during
[ADR 0021](0021-venue-screen-split-and-geocoding.md)'s geocoding run. This
ADR documents what was found, how each was confirmed (not guessed), and
what was fixed — in the database and in the code that produced it.

## Findings and fixes

### 1. Six fake venue rows from a pre-fix LLM garbling bug

`io/scrapers/paris_cine_info.py` already discards the LLM's own
title/venue guesses for a commented showtime in favor of the API's known
facts — a fix confirmed still in place today by
`test_fetch_events_discards_the_llms_title_venue_and_time_guesses`. Six
rows predating that fix survived uncleaned: venue names `"null"`, `"No venue specified"`, `"Not specified in the text"`, `"Little Films Festival"`, `"Little Films Festival venue"`, and `"Animo rigolo"` (a real
film title that got swapped into the venue field) — all really the same
handful of physical cinemas (`Les 7 Parnassiens`, `Chaplin Denfert`, `Les 5 Caumartin`) screening a themed children's summer program ("Little Films
Festival"), corrupted 11 different ways depending on which piece of text
the LLM merged into which field.

Each of the 11 affected screenings was traced individually via its
booking URL (`achat.multicine.fr/parnassiens/...`,
`.../denfert/...`, `.../cinqcaumartin/...` — the subdomain identifies the
real cinema unambiguously) and cross-checked against independent web
research confirming "Animo rigolo", "Rita et Crocodile", and "Les
Tourouges et les Toubleus" are all real, existing films/programs, not
garbled text. Every target `Venue` and `Film` row already existed
cleanly from other, correctly-parsed screenings of the same festival —
`scripts/fix_little_films_festival_corruption.py` only repoints, it
creates nothing.

### 2. Self-inflicted geocoding contamination — 82+ venues (~30% of the table)

Every column looked "trusted" (`geocode_source = ADDRESS`,
`needs_review = false`) while pointing at Berlin, São Paulo, Karachi,
Trondheim, Rome, Venice, London, and a scatter of unrelated small French
towns — none of these venues carry a `paris_cine_info_tid` (the only
source that ever legitimately scrapes an address), ruling out a scraper
bug.

Root cause, fully traced: during ADR 0021's real geocoding run, an
Île-de-France bounding box was added to fix wildly-wrong tier-2
(name-search) matches, and every `NAME_SEARCH`-sourced venue's
coordinates were reset to re-run geocoding with the fix. That reset
cleared `latitude`/`longitude`/`geocode_source`/`needs_review` but **not
`address`** — so a wrong tier-2 guess already backfilled into
`venue.address` survived. On the next `geocode-venues` run, that now
non-null (but wrong) address made **tier 1** (direct address lookup) run
instead of tier 2 — and tier 1 always sets `needs_review=False`, since a
direct lookup is normally precise by construction. The wrong guess was
laundered into a falsely "confirmed" result.

One case slipped past even a naive "does this address contain an
Île-de-France postal code" filter: `UGC Ciné Cité La Défense` ended up
with `UGC Ciné Cité Cergy-le-Haut`'s address — wrong, but still a real
IDF postal code (Cergy, 95), so a postal-code-only contamination check
missed it. Caught instead by a `pg_trgm` similarity audit
(`similarity(a.name, b.name)`, now that
[ADR 0022](0022-postgres-extensions.md) added the extension) surfacing
the two venues' identical addresses.

**Code fix**: `VenueRepository.save_geocoding` no longer backfills
`venue.address` from an *unconfirmed* (`needs_review=True`) match —
only a confirmed cinema-tagged result can seed the address field. This
closes the exact hole that let a bad guess get laundered into "trusted"
data on a later pass.

**Data fix**: `scripts/fix_contaminated_venue_addresses.py` clears
`address`/`location`/`geocode_source`/`needs_review` for every venue with
no `paris_cine_info_tid` and an address outside Île-de-France (82 rows);
`scripts/fix_venue_fragmentation_round2.py` clears the one IDF-postal-code
false negative (`UGC Ciné Cité La Défense`) directly. Cleared venues are
left `NULL`, ready for the manual research pass
([ADR 0021](0021-venue-screen-split-and-geocoding.md)'s original plan) —
this ADR does not attempt to guess replacement values.

### 3. Two rounds of plain name/address-collision fragmentation

A `GROUP BY address HAVING count(*) > 1` audit found four venue pairs
sharing an exact street address under different scraper wording
(`Chaplin` / `Chaplin Denfert`, `Fondation Jérôme Seydoux-Pathé` /
`Cinémathèque Pathé, Fondation Jérôme Seydoux-Pathé`, `Cinepal' Palaiseau`
/ `CinéPal'`, `Cinéma Jacques Prévert` / `Théâtre et cinéma Jacques-Prévert`) — the same class of bug as
[ADR 0011](0011-retire-mk2-louxor.md)/[ADR 0021](0021-venue-screen-split-and-geocoding.md),
just never caught before since neither name was an already-known alias.
Fixed via `scripts/fix_venue_address_collision_duplicates.py`.

A follow-up `pg_trgm` similarity sweep found five more pairs, each
confirmed via an identical address or strong corroborating evidence (a
stable `paris_cine_info_tid`, source mix, screening volume): `7 Batignolles` / `Les 7 Batignolles`, `Christine Cinema Club` / `Christine Cinéma Club` (an accent-only variant `normalize_text` doesn't unify,
since it never strips accents — only `unaccent()`-backed *search*, not
natural-key normalization, does that), `UGC Ciné Cité La Défense` / `UGC La Défense`, `UGC Rotonde` / `Rotonde`, `CGR Paris Lilas` / `CGR Paris - Lilas`. Fixed via `scripts/fix_venue_fragmentation_round2.py`. A handful
of lower-confidence `pg_trgm` matches remain unmerged (e.g. `Le Trianon`
/ `Trianon`, `Le Méliès` / `Méliès` — both extremely common French cinema
names, plausibly different real venues in different towns) — deferred to
manual confirmation during the venue research pass rather than merged on
name similarity alone, since a wrong merge is destructive
(`VenueRepository.merge_venue` deletes the loser row) while leaving two
separate rows is merely an inefficiency.

## Net effect

|                                         | Before | After                                               |
| --------------------------------------- | ------ | --------------------------------------------------- |
| Venue rows                              | 289    | 274                                                 |
| Film rows                               | 808    | 804                                                 |
| Screening rows                          | 41,231 | 41,231 (unchanged — every fix repoints, none drops) |
| Venues with a non-Île-de-France address | 87     | 0                                                   |
| Venues flagged `needs_review`           | 28     | 12                                                  |

## Consequences

- `VenueRepository.save_geocoding`'s stricter backfill rule is a
  permanent behavior change: an unconfirmed name-search match no longer
  ever writes to `venue.address`, even implicitly. Verified via two new
  regression tests (`test_save_geocoding_backfills_a_missing_address_from_a_confirmed_result`,
  `test_save_geocoding_does_not_backfill_address_from_an_unconfirmed_result`).
- The ~90 venues left with cleared address/location fields are exactly
  the current scope of the manual research pass (browser-assisted, one
  time, not re-run every scrape — see
  [ADR 0021](0021-venue-screen-split-and-geocoding.md)'s framing of
  venues as effectively static data).
- The four `scripts/fix_*.py` one-off cleanups are historical record, not
  meant to be re-run — each targets specific row IDs confirmed at the
  time of writing, the same convention as
  `fix_mk2_venue_fragmentation.py`/`fix_cinematheque_fragmentation.py`.
- `Film.title_key`/`Venue.slug` natural-key normalization
  (`core/dedup.py::normalize_text`) still does not strip accents — an
  accent-only variant of an existing name (like `Christine Cinéma Club`
  vs `Christine Cinema Club`) can still fragment silently. Search
  (`EventRepository.search`) is accent-insensitive since ADR 0022; natural
  -key matching at ingestion time is not. Worth revisiting if this
  recurs.
