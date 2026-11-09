# 0021 — Venue/Screen schema split, venue data audit, two-tier geocoding

- Status: accepted
- Date: 2026-07-16

## Context

With pgweb available ([ADR 0018](0018-postgres-gui-client-pgweb.md)), the
`venue` table's real data could be inspected directly for the first time
since the Postgres migration ([ADR 0014](0014-postgres-migration.md)). Two
goals drove the audit: every venue needs a valid address and GPS coordinates
for a future one-point-per-cinema interactive map (see
[`docs/vision.md`](../vision.md)), and the data itself needed a general
correctness pass — no missing or spurious fields, every cinema's data as
complete as scraping can make it, and its official website when known.

## Audit findings

Inspecting all 291 `venue` rows surfaced four concrete issues:

1. **La Cinémathèque française was fragmented into three rows**: the
   canonical venue plus one row each for "Salle Georges Franju" and "Salle
   Henri Langlois" — its two screening rooms, each masquerading as its own
   physical cinema. Same class of bug as the already-fixed MK2 fragmentation
   ([ADR 0011](0011-retire-mk2-louxor.md)), but structural rather than a
   naming variant: no amount of alias matching fixes "a room mistaken for a
   venue," because the underlying schema had no concept of a room at all.
1. **Room-level detail was silently discarded for every multi-screen venue.**
   `Venue` carried `seat_count`/`screen_width_m`/`screen_height_m` as
   single, venue-level fields. The Paris Ciné Info scraper's
   `get_pcitheatre.php` endpoint (see
   [ADR 0007](0007-paris-cine-info.md)) returns this detail per
   `(theatre_id, screen_id)` pair — one call per *room*, not per venue — so a
   multiplex reporting several distinct screens this run had nowhere
   unambiguous to put more than one room's numbers and dropped them
   entirely (`fetch_venue_details`'s previous `include_room=len(screens) == 1` guard). The endpoint's own `screen_name`/`screen_id` fields, which
   would have disambiguated exactly this, were never even parsed.
1. **No further name-based fragmentation beyond the already-known MK2 case**
   — confirmed by re-running the same alias-matching logic
   ([ADR 0011](0011-retire-mk2-louxor.md)) as a one-off audit query, not
   just trusting that the earlier fix was exhaustive.
1. **No address or coordinates anywhere.** `Venue` had never carried either
   field — every one of the 291 rows was equally ungeocoded, not a partial
   gap.

## Decision

### Split `Venue` into `Venue` (cinema) + `Screen` (room)

`Venue` now models exactly one physical building: `id`, `slug`, `name`,
`kind`, `address`, `latitude`/`longitude`/`geocode_source`/`needs_review`
(the new geocoding fields, below), `website`, `accepted_passes`,
`paris_cine_info_tid`. A new `Screen` table (`venue_id` FK,
`paris_cine_info_screen_id` FK-like external id, `name`, `seat_count`,
`screen_width_m`, `screen_height_m`) carries every room's own detail — a
multiplex's several screens are several `Screen` rows under one `Venue`, not
several `Venue` rows or several ambiguous columns on one.

Migrated via `7868904a30b8` (down-revision `01cce00fd718`, the fresh
Postgres-native initial schema — see
[ADR 0014](0014-postgres-migration.md)): creates `screen`, adds the
geocoding columns (`needs_review` needed a `server_default=false` — the 291
existing `venue` rows are real data, not an empty schema), copies every
venue's pre-existing room fields into exactly one `Screen` row per venue
that had any of them set, then drops the three venue-level columns.
Cinémathèque's two room-labeled rows were merged into the canonical one via
a one-off script (`scripts/fix_cinematheque_fragmentation.py`, same
merge-then-repoint pattern as `scripts/fix_mk2_venue_fragmentation.py`),
creating a `Screen` for each retired room name first so that detail is
preserved, not lost, by the merge.

### Scraper: fetch every screen, not just one representative

`ParisCineInfoScraper.fetch_venue_details` previously fetched only one
`(theatre_id, screen_id)` pair per venue (`next(iter(screens))`) and
discarded room fields whenever a venue reported more than one pair this run.
It now fetches every known pair concurrently and merges the results
(`_merge_venue_details`): `address`/`website` (cinema-level, identical
across rooms) take the first non-`None` value found; every fetched room
becomes its own `ScreenDetail` in the merged `VenueDetail.screens` tuple.
`VenueRepository._upsert_screens` persists each one, matched by
`(venue_id, paris_cine_info_screen_id)` so a room is recognized and updated
in place across scrape runs rather than duplicated — a screen with no
external id (the source's own room identifier) is skipped rather than risk
a duplicate row every run.

### Geocoding: two free, keyless tiers, not a paid API

With under 300 venues total, a free tier easily covers the whole dataset —
no paid geocoding API was justified.

- **Tier 1 — address lookup**, for any venue with a known street address:
  the French government's BAN/Géoplateforme geocoder
  (`https://data.geopf.fr/geocodage/search`), free, keyless, official.
  Supersedes the older `api-adresse.data.gouv.fr` endpoint, decommissioned
  end of January 2026 and redirected to the new host — confirmed live
  before use. An address match is precise by construction and never
  flagged for review.
- **Tier 2 — name search fallback**, for a venue with no address at all:
  Photon (`https://photon.komoot.io/api/`), an OSM-based POI search, also
  free and keyless, restricted to an Île-de-France `bbox` (this project
  covers only Paris/IDF cinemas). The bounding box was added after the
  first real run: an unrestricted search for a generic single-word venue
  name is unreliable well beyond the already-known "wrong French place"
  failure mode — confirmed live, "Apollo" resolved to a village in
  Pennsylvania, "Capitole" to a square in Rome, "Atmosphère" to a town in
  Brazil, entirely different continents, not just the wrong Paris
  district. The bbox doesn't guarantee a *correct* match within the
  region, hence a match is still only trusted outright when OSM itself
  tags the result as a cinema (`amenity=cinema`); anything else is still
  stored (a best-effort coordinate beats none for a first map draft) but
  flagged `Venue.needs_review = True` for manual confirmation via pgweb
  rather than silently accepted. Photon's structured address components
  also backfill `Venue.address` when the venue had none — partially
  closing audit finding 4 for name-search venues too, not just
  address-lookup ones.

Both services are called through `io/geocoding.py`'s `geocode_venue`, rate
limited to roughly 5 req/s (`RATE_LIMIT_DELAY_SECONDS`) out of courtesy to
two free public services with no formal SLA. The `geocode-venues` CLI
command (`main.py`) is idempotent — it only ever considers venues still
missing coordinates, so re-running it after manually correcting a
`needs_review` row via pgweb does not re-touch that row. A single tier's
HTTP request failing outright (confirmed live: the BAN API returned a
transient 504 mid-run) is caught and treated as "this tier found nothing"
rather than aborting the whole run — the same "one bad item never aborts a
run" posture as `io/scrapers/base.py`'s `structure_via_llm`.

## Real run results

Run against the 289 migrated venues (two passes: the bbox restriction and
the transient-failure handling above were both added after the first pass
surfaced their absence):

- **276/289 geocoded (95.5%)**, 280/289 with a known address (96.9%, up
  from 0 before this work).
- **28 flagged `needs_review`** (down from 116 before the Île-de-France
  `bbox` restriction was added — see tier 2 above) — every remaining
  flagged coordinate plots inside Île-de-France, not a wrong continent.
- **13 unresolved**, on inspection all legitimate: a handful of small/
  generic-named venues Photon has no IDF match for at all, plus a few
  scraper-side placeholder "venue" strings (e.g. "Little Films Festival
  venue", "Not specified in the text") that predate this work and are a
  separate, LLM-extraction-side data-quality issue — out of scope here, not
  silently geocoded into a plausible-looking but meaningless point.

## Consequences

- `Venue.seat_count`/`screen_width_m`/`screen_height_m` no longer exist —
  any code or query expecting them must go through `Screen` instead, joined
  on `venue_id`.
- A `needs_review = True` venue has a best-effort, not a trusted, coordinate
  — the future map should visually distinguish these (or exclude them)
  until manually confirmed, rather than plotting a possibly-wrong point.
- The Cinémathèque fix is a one-off script, not a general dedup rule — a
  future room-labeled fragmentation of the same shape would need its own
  pass (or a proper heuristic) rather than being caught automatically.
- Coordinates and address are best-effort from free public data — accuracy
  is bounded by what BAN/OSM know, not independently verified per venue.
