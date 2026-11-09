# 0028 — CNC exploitation visas as the official film reference

- Status: proposed
- Date: 2026-08-03

## Context

`Film` rows are keyed on `title_key`, a normalized announced title
(`core/dedup.py::normalize_text` — casing, whitespace and apostrophes
only, not punctuation). [`docs/coverage-matrix.md`](../coverage-matrix.md)
records this as the last remaining open data-quality item, with live
evidence: "Vaiana : La Légende du bout du monde" and "Vaiana, la légende du
bout du monde" produced two `Film` rows resolving to the same TMDB film,
colliding on `Film.tmdb_id`'s unique constraint — **4 183 of that run's
4 455 collisions came from that single title pair**. The shipped fix
(`find_film_by_tmdb_id` / `merge_film`) heals the collision after the fact;
the key itself is unchanged.

TMDB is the only film authority in the project today. It is excellent for
metadata (synopsis, runtime, images, cast) but is a community database
whose French titles are user-contributed, which is precisely the axis on
which the fragmentation occurs.

## Source recon (verified live, 2026-08-03)

"Liste des visas d'exploitations cinématographiques délivrés de 1945 à
2025", published by the CNC on `data.gouv.fr`, last updated 2025-11-13,
available as CSV and XLSX. Semicolon-separated, columns confirmed against
the real payload:

```
Titre;Réalisation;Production;Format;Date;Nationalité;N° de visa;Décision;Localisation du dossier visa
```

Sample rows:

```
À bout de course. Running on empty;Sidney Lumet;Lorimar Film Entertainment - Double Play;Long-métrage;1988;États-Unis;68657;Tout public;Archives nationales
About love Tokyo. Ai ni tsuite Tokyo;Mitsuo Yanagimachi;Seisaku Iinkai;Long-métrage;1993;Japon;81776;Tout public;Archives nationales
```

What this dataset **does** provide: the visa number (a
state-assigned per-work identifier), the official French title with the
original title appended after a period, the director, the production
company, `Format` (`Long-métrage` / `Court-métrage`), nationality, and the
classification decision (`Tout public`, and by extension the age
restrictions).

What it **does not** provide, contrary to an initial assumption worth
recording so it is not re-explored: `Date` is a **year**, not a release
date. This dataset cannot serve as a source of French theatrical release
dates.

## Decision

### Adopt the visas dataset as a reference source, in a bounded role

Loaded by `io/reference/cnc_visas.py` under the layer defined in
[ADR 0026](0026-official-reference-data-layer.md). It contributes, at tier
`OFFICIAL_REGISTRY` ([ADR 0027](0027-field-provenance-and-source-tiers.md)):

- `Film.cnc_visa` — the official identifier.
- `Film.french_title` — the canonical French title, which becomes the
  target for title normalization rather than whichever wording a scraper
  announced.
- `Film.is_short_film` — derived from `Format`.
- `Film.nationality`, `Film.age_rating`.

TMDB **remains** the primary source of film metadata. This ADR does not
demote it; the two are complementary and their fields barely overlap.

### The title-fragmentation fix, stated honestly

Adding an official title reference does **not** on its own fix
`normalize_text`. Matching an announced title to a CNC visa row is the same
fuzzy-matching problem as matching it to a TMDB entry — with the same
punctuation sensitivity. The referential improves the *target* of matching
(a canonical, official French title instead of "whatever was announced
first"), not the matching function.

So this ADR is paired with a direct fix to `normalize_text`: fold
punctuation (`:`, `,`, `-`, `.`) and diacritics in the normalized key.
That is the change that actually collapses the Vaiana pair, and it should
be shipped and measured on its own rather than credited to the referential.

### Short films are a first-class signal, not a footnote

`Format = Court-métrage` is directly actionable: short-film programmes are
one of the screening categories the project exists to surface
(see [`CLAUDE.md`](../../CLAUDE.md)). A screening whose film is a
CNC-registered short is a strong, officially-sourced specialness signal
that the current rule set has no way to derive.

## Consequences

- **Scale changes the loading strategy.** The visas dataset covers 1945 to
  today across all formats — orders of magnitude larger than the ~2 000
  establishments in [ADR 0026](0026-official-reference-data-layer.md). It
  should not be loaded wholesale into `Film`. It is a *lookup* referential:
  indexed once (title key → visa row), consulted when a `Film` is created
  or enriched. Whether that index lives in Postgres or as a prepared local
  artifact is an implementation choice, deliberately left open here.
- **Matching failures must be silent and safe.** A film with no visa match
  keeps its TMDB-derived data unchanged. Absence of a visa is not evidence
  of anything — foreign films screened without French commercial
  exploitation, festival-only prints, and very recent releases can all
  legitimately be missing.
- **Nationality and classification are new, unused fields.** They are
  cheap to store alongside the rest and plausibly useful for the bot's
  natural-language Q&A, but nothing consumes them at introduction. Flagged
  explicitly rather than presented as a benefit.
- **Two title fields on `Film`.** `title` (as first announced, display
  form) and `french_title` (official). The digest and bot should prefer the
  official one when present; that is a user-visible change to French output
  and should be verified on real data before shipping.
