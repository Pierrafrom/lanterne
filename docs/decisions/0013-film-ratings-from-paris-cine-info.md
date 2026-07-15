# 0013 — Source every film rating from Paris Ciné Info, not the rating sites themselves

- Status: accepted
- Date: 2026-07-15

## Context

The goal: enrich each `Film` with a backdrop image (for a Letterboxd-style
visual display) and ratings from IMDb, Allociné, SensCritique, Rotten
Tomatoes, and Letterboxd, with vote counts and per-star breakdowns for
graphing, mirroring the `duncanlang/Letterboxd-Extras` browser extension.

That extension's ratings logic only works because it runs *inside* a
browser already on a Letterboxd page:

- **IMDb** — an unofficial GraphQL API (`api.graphql.imdb.com`), keyed by
  IMDb id, with a full histogram and vote count.
- **Rotten Tomatoes / Allociné** — no API at all: a JSON blob embedded in
  the RT page's HTML, and raw CSS-selector scraping of Allociné's HTML.
- **SensCritique** — an unofficial GraphQL API
  (`apollo.senscritique.com`), with a full-text search fallback when no id
  is known.
- **Wikidata SPARQL** (`query.wikidata.org/sparql`, public and
  legitimate) — cross-references an IMDb id to a Rotten Tomatoes id, an
  Allociné id, and (sometimes) a SensCritique id.
- **Letterboxd's own rating** — never fetched remotely by the extension at
  all; it is already on the page it is injected into. Getting it for a
  standalone bot has no precedent in that codebase.

None of this transfers cleanly to an always-on backend bot: RT, Allociné,
and SensCritique mean scraping HTML or undocumented endpoints outside
those sites' ToS — fragile to redesigns, and a materially different risk
profile for a centralized bot hitting these endpoints from one IP on a
schedule than for traffic distributed across thousands of individual
browser-extension users. Letterboxd's own rating has no working approach
in the reference implementation at all.

## Evidence

Paris Ciné Info (`paris-cine.info`, already an authenticated source — see
[ADR 0007](0007-paris-cine-info.md)) turned out to already carry this data
natively. Confirmed live (2026-07-15): `get_movies.php`'s full catalogue
(no `events` filter — the same call `ParisCineInfoScraper._fetch_movies`
already makes) returns, per film, pre-aggregated ratings for IMDb,
Allociné (press *and* audience), SensCritique, Rotten Tomatoes, Metacritic
(unrequested bonus, included since it costs nothing extra), and
Letterboxd, plus the film's IMDb id and URL slugs for
SensCritique/RT/Metacritic/Letterboxd:

```json
{
  "i_id": "0055852", "im_r": 7.8,
  "ap_r": 0, "as_r": 4.1,
  "sc_r": 7.4, "sc_u": "Cleo_de_5_a_7/446018",
  "rt_r": 93, "rt_u": "/m/cleo_de_5_a_7",
  "mc_r": 87, "mc_u": "/cleo-from-5-to-7",
  "lb_r": 4.2, "lb_u": "cleo-from-5-to-7"
}
```

`0` is Paris Ciné Info's own "no data for this source" convention (`ap_r`
above, alongside a populated `as_r` on the same entry) rather than a
missing field or `null`. Across the full catalogue (496 films, confirmed
live), every entry carries an IMDb id and ~74% carry SensCritique/RT/
Letterboxd all at once. The four external URL formats were each verified
live (HTTP 200): `senscritique.com/film/{sc_u}`,
`rottentomatoes.com{rt_u}`, `metacritic.com/movie{mc_u}/`,
`letterboxd.com/film/{lb_u}/`.

TMDB's `/movie/{id}` details response — already fetched for every film's
enrichment — also natively returns `imdb_id` and `backdrop_path` as
top-level fields (confirmed live), with no extra API call.

## Decision

Source every film rating exclusively from Paris Ciné Info's catalogue
(`ParisCineInfoScraper.fetch_film_ratings`, parsed by `parse_film_ratings`
in `io/scrapers/paris_cine_info.py`), matched to a `Film` row by
`Film.imdb_id` (set during TMDB enrichment, `io/tmdb.py`). This project
never queries IMDb, Allociné, SensCritique, Rotten Tomatoes, or Letterboxd
directly. Backdrop comes from TMDB's existing details call.

**Explicit trade-off, confirmed with the project owner:** this gives a
single aggregate score per source, on that source's own native scale (see
`RatingSource`'s docstring in `core/models.py`) — no vote counts and no
histogram. Real Letterboxd-style bar charts would need IMDb's own
unofficial GraphQL API directly (cheap, since the IMDb id is already
known) at minimum, and the fragile RT/Allociné/SensCritique/Letterboxd
scraping this decision avoids for the other four. Descoped for now in
exchange for zero new scraping surface and zero new ToS exposure — same
account, same session infrastructure ADR 0007 already authorized.

Schema: a normalized `FilmRating` table, one row per `(film, source)` —
mirrors `EventSighting`'s one-row-per-(event, source) shape rather than
widening `Film` with a column per rating source, so a new source (or a
future histogram field) needs no migration to `Film` itself.

Enrichment is a whole-catalogue, end-of-run pass
(`IngestionPipeline._apply_film_ratings`), detected via `isinstance`
against the `FilmRatingSource` Protocol — the same "optional capability"
shape as `VenuePassSource`/`VenueDetailSource`, not a special case.

## Consequences

- Ratings coverage is bounded to films Paris Ciné Info currently lists as
  showing somewhere in its partner network — a film outside that set (an
  OFFI-only suburb screening, or a film not currently playing anywhere in
  the partner network) simply has no ratings yet, picked up automatically
  the next run it starts showing there. No fallback scraping is
  implemented for this gap.
- No vote counts, no histogram, no bar-chart-ready distribution data for
  any of the six sources — only single aggregate numbers. Revisit via a
  direct IMDb GraphQL call (the cheapest of the four originally
  considered) if the graphing ambition becomes a real requirement later.
- `Film.imdb_id` is a second natural-key-like field alongside `tmdb_id`
  (both unique, both populated together during TMDB enrichment) — a film
  that fails TMDB enrichment gets no ratings either, same dependency
  `Venue` enrichment already has on a stored screening existing first.
