# 0023 — JSONB `none_as_null` bug: three columns storing a JSON `null` literal instead of SQL NULL

- Status: accepted
- Date: 2026-07-17

## Context

A venue-table data-quality audit (the first step of a broader manual
data-cleanup pass, see
[`docs/decisions/0024-venue-data-cleanup.md`](0024-venue-data-cleanup.md))
found `Venue.accepted_passes` non-`NULL` for all 289 rows — including 180
venues that had never actually been enriched with pass data. Inspecting
`jsonb_typeof(accepted_passes)` revealed the real state: 180 rows held the
JSON *literal* `null` (a real, non-empty JSONB value whose type is
`"null"`), not a `NULL` column. A plain `accepted_passes IS NULL` check —
used nowhere in this codebase today, but the obvious thing to reach for —
would have silently returned zero rows for all 180.

Root cause: SQLAlchemy's `JSON`/`JSONB` type has a `none_as_null`
constructor flag, **default `False`**. With the default, assigning Python
`None` to a JSON-typed column serializes it as the JSON `null` literal on
INSERT/UPDATE, not as SQL `NULL` — confirmed against SQLAlchemy's own
docs (`core/type_basics.html`, "Configuring None Persistence in JSON
Type"). `Venue.accepted_passes: list[str] | None = Field(default=None, sa_type=JSONB)` never set `none_as_null=True`, so every venue created
without pass data got the JSON `null` literal instead of a real absence.

Grepping the schema for the same `sa_type=JSONB` pattern (no
`none_as_null`) found two more affected columns, with much larger blast
radii:

| Column                               | Affected rows | Total rows |
| ------------------------------------ | ------------- | ---------- |
| `Venue.accepted_passes`              | 180           | 289        |
| `Film.genres`                        | 102           | 808        |
| `ScreeningEvent.specialness_reasons` | 36,509        | 41,231     |

`specialness_reasons` in particular — nearly every ordinary (non-special)
screening — was affected, since ordinary screenings never set this field
and always hit the buggy default path.

## Decision

Set `none_as_null=True` on all three `JSONB` field definitions in
`core/models.py`:

```python
accepted_passes: list[str] | None = Field(
    default=None, sa_type=JSONB(none_as_null=True)
)
```

Migration `f28833a889dc` converts the existing artifact rows back to true
SQL NULL for all three columns:

```sql
UPDATE venue SET accepted_passes = NULL WHERE accepted_passes = 'null'::jsonb;
UPDATE film SET genres = NULL WHERE genres = 'null'::jsonb;
UPDATE screeningevent SET specialness_reasons = NULL
  WHERE specialness_reasons = 'null'::jsonb;
```

No data is lost — a JSON `null` and a SQL `NULL` both mean "no value
known" for a nullable field; converting one representation to the other is
purely a representation fix, not a semantic change. `downgrade()` is
therefore a documented no-op rather than reversing the conversion.

## Consequences

- Any *future* `col IS NULL` filter on these three columns (or any new
  `sa_type=JSONB` column added later) behaves correctly without this
  footgun — as long as `none_as_null=True` is remembered. Worth checking
  for the same pattern (`sa_type=JSONB` with no `none_as_null`) whenever a
  new JSONB column is added.
- Confirmed via a live regression test per column
  (`test_repository.py`): ingesting a venue/film/screening with no
  explicit value for the field and asserting
  `jsonb_typeof(column) IS NULL` (which is itself `NULL` only for a true
  SQL `NULL` — `jsonb_typeof('null'::jsonb)` returns the string
  `"null"`).
- This was found by accident while auditing `Venue` specifically, but the
  same bug pattern could plausibly exist in other yet-unaudited tables in
  future projects using this `sa_type=JSONB` shorthand — worth a project
  convention note if this pattern reappears.
