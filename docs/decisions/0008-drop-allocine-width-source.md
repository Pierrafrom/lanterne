# 0008 — Drop AlloCiné as the all-screenings width source

- Status: accepted
- Date: 2026-07-13

## Context

As part of the shift from "special screenings only" to "every screening,
with a specialness signal computed after ingestion" (see
[`architecture.md`](../architecture.md) once updated), AlloCiné was the
first candidate evaluated as the wide-coverage source: it aggregates
showtimes for nearly every commercial and independent cinema in Paris/IDF,
which none of the current bespoke scrapers individually provide.

Two AlloCiné access paths were investigated:

1. **`www.allocine.fr/_/showtimes/theater-{id}/d-{date}/p-{page}`** — an
   internal JSON endpoint (confirmed via the source of the community
   package `allocine-seances`, PyPI, and reproduced live: a real request
   returns paginated JSON with `movie.title`, `showtimes[].startsAt`,
   `showtimes[].diffusionVersion`, and — notably — `customFlags.isPremiere`
   / `customFlags.weeklyOuting`, genuine specialness signals sourced from
   AlloCiné's own classification). This would have been Level 2 (no LLM),
   the cheapest and most robust tier per
   [`scraping-strategy.md`](../scraping-strategy.md)'s decision tree.
1. **`www.allocine.fr/seance/salle_gen_csalle={id}.html`** — the classic
   server-rendered showtimes calendar (multi-week, per cinema), a Level 4
   fallback (HTML, no confirmed embedded JSON) if the JSON endpoint were
   ruled out.

## Why the JSON endpoint is disallowed

`www.allocine.fr/robots.txt`, fetched and read verbatim, disallows `/_/`
for the generic `User-Agent: *` block:

```
User-agent:*
Disallow: /cdn-cgi/
Disallow: /rechercher/
Disallow: /salle/recherche/
...
Disallow: /_/
Disallow: /_video/partner/
```

This is an explicit crawl-permission signal, not merely a technical
anti-bot obstacle to route around — the same standard already applied to
Pathé (dropped on a plain 403, "respecting that signal rather than working
around it", per `scraping-strategy.md`'s "be a good citizen" rule). The
`/_/showtimes/...` endpoint falls squarely under this disallow, so it is
not an option regardless of technical feasibility.

(Separately, `www.allocine.fr` sits behind Cloudflare bot management: a
first request typically succeeds, but a short burst of follow-up requests
starts timing out — a behavioral/rate-based response, not a hard block.
This reinforced but was not the deciding factor; the robots.txt disallow
alone is sufficient to drop this path.)

## Why the HTML fallback wasn't pursued either

The `/seance/` calendar page is not disallowed by robots.txt, but by the
time this was reached, two better-fitting sources were identified and
partially validated (see below), making a third, Cloudflare-throttled,
structurally-unverified Level 4 source unnecessary. It was not
implemented; revisit only if the sources below prove insufficient in the
Phase 0 coverage audit.

## Decision

Drop AlloCiné entirely as a source (JSON and HTML alike). The width
problem is solved instead by:

- **Extending Paris Ciné Info's existing scope** (see
  [ADR 0007](0007-paris-cine-info.md)): `get_movies.php` without the
  `events=true` filter returns the full film catalogue across its partner
  network — confirmed live at **494 films**, versus **27** under the
  current `events=true` filter — using the same authenticated, already
  ethically-vetted API, at zero new integration or legal-risk cost.
- **L'Officiel des spectacles (offi.fr)** as a complementary width source:
  `robots.txt` places no restriction on `/cinema/` paths, and its film
  listing already carries a ready-made rarity signal ("à Paris: X salles,
  autour de Paris: Y salles") directly useful to the specialness
  classifier, plus visible labels (e.g. "Réédition").

## Consequences

- No AlloCiné integration, ever, under the current robots.txt — revisit
  only if AlloCiné publishes a different policy or exposes a compliant
  API.
- The specialness-classifier signal AlloCiné would have given for free
  (`customFlags.isPremiere`/`weeklyOuting`) is not available from either
  replacement source at the same granularity; Paris Ciné Info's `com`
  field (now read for every showtime, not only the previously
  LLM-classified ones) and offi.fr's salle-count/label signals are the
  substitutes — see the specialness pipeline design once implemented.
- Paris Ciné Info's partner network (confirmed to include UGC, Pathé, MK2,
  Le Grand Rex, and independents) plus offi.fr's broader Paris/IDF listing
  are expected to jointly approximate or exceed AlloCiné's practical
  coverage for this project's purposes — to be confirmed by the Phase 0
  coverage matrix, still in progress.
