# Testing

## Running the suite

```fish
uv run pytest                       # full suite + coverage (fails under 80%)
uv run pytest tests/test_qa.py      # one file
uv run pytest -k dedup              # tests matching a keyword
uv run pytest tests/test_qa.py::test_format_qa_answer_without_events
```

Coverage is enforced at **80%** project-wide (`--cov-fail-under=80` in
`pyproject.toml`); the suite currently sits around 90%+. The uncovered remainder
is the composition roots in `main.py` (real network/Telegram/Ollama wiring),
which are validated manually rather than unit-tested.

## Conventions

- **Async tests** run under `pytest-asyncio` in `auto` mode — just write
  `async def test_...`; no decorator needed.
- **No network in tests.** External I/O is always mocked or faked:
  - the **database** uses a real in-memory SQLite via the shared `database` /
    `session` fixtures in `tests/conftest.py` (a `StaticPool` keeps the schema
    across sessions);
  - **HTTP** (scrapers, TMDB) uses `MagicMock` clients with `AsyncMock` `.get`;
  - the **LLM** (Instructor) and the **Telegram bot** are mocked.
- **Scraper parsing** is tested against committed, trimmed real-markup fixtures
  in `tests/fixtures/` (one or more per source, e.g. `cinematheque_*.html`,
  `forumdesimages_*.html` — each trimmed from a real fetch of the live site,
  not hand-invented markup). A site redesign breaks the parse test — the
  intended early-warning signal (see [scraping-strategy.md](scraping-strategy.md)).
  Retired sources (MK2, Le Louxor, Le Champo — see
  [ADR 0011](decisions/0011-retire-mk2-louxor.md) and
  [ADR 0012](decisions/0012-retire-lechampo.md)) had their fixtures and tests
  deleted along with the scraper.
- **One behaviour per test**, named `test_<behaviour>_<condition>`.

## What is tested where

| Area                               | Tests                                                                                                                                                            |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Domain models, dedup key           | `test_models.py`, `test_dedup.py`                                                                                                                                |
| Persistence, dedup ingest, search  | `test_db.py`, `test_repository.py`, `test_ingest.py`                                                                                                             |
| Scrapers (parsing + orchestration) | `test_scrapers.py`, `test_premiereprojo.py`, `test_forumdesimages.py`, `test_fondationpathe.py`, `test_lavillette.py`, `test_offi.py`, `test_paris_cine_info.py` |
| LLM extractor & guards & eval      | `test_llm.py`, `test_validation.py`, `test_evaluation.py`                                                                                                        |
| TMDB enrichment                    | `test_tmdb.py`                                                                                                                                                   |
| Ingestion pipeline                 | `test_pipeline.py`                                                                                                                                               |
| Digest & Q&A formatting/search     | `test_digest.py`, `test_qa.py`                                                                                                                                   |
| Bot handlers & broadcast           | `test_bot.py`                                                                                                                                                    |
| CLI & JSONL logging                | `test_main.py`                                                                                                                                                   |

## Quality gates (run before every commit)

The pre-commit hook runs them automatically; to run by hand:

```fish
uv run ruff check .       # lint — must be empty (zero warnings)
uv run ruff format .      # formatting
uv run mypy src           # strict type checking
uv run pytest             # tests + coverage
```

## Evaluating the LLM extraction (offline harness)

Unit tests never call the real LLM. To measure the *quality* of the configured
Ollama model on the extraction task, run the golden-dataset harness (real LLM
calls, so it needs a reachable `OLLAMA_BASE_URL`):

```fish
uv run lanterne eval-extraction
```

It runs the extractor over `eval/golden_extractions.json` (announcement texts
paired with the expected structured event) and reports per-field accuracy,
exact-match rate, and mean latency. Run it before and after any model or
prompt change, and when comparing candidate models.

To grow the dataset, append a case to the JSON file: give it a short unique
`id`, put in `raw_text` exactly what a scraper would hand the LLM (including a
`Reference date:` line when the announcement omits the year), and fill
`expected` with the correct extraction (times in UTC).

## Evaluating the specialness classifier (offline, no I/O)

The rule-based specialness classifier (`core/specialness.py`, see
[ADR 0009](decisions/0009-specialness-rules-only.md)) has its own golden
-dataset harness — unlike the extraction one, it needs no LLM or database,
since the classifier and its inputs are pure, in-memory data:

```fish
uv run lanterne eval-specialness
```

It runs the classifier over `eval/golden_specialness.json` (labeled
screenings: venue kind, team presence, cycle name, release year, and the two
aggregate counts `distinct_venue_count`/`weekly_showing_count`) and reports
accuracy, precision, and recall against each case's `expected_special` label.

The seeded dataset is a handful of hand-built illustrative cases, one per
rule plus two negatives — not yet validated against a real scrape's actual
distribution of ordinary-vs-noteworthy screenings (see `coverage-matrix.md`'s
"Specialness classifier tuning" open item). To grow it with real examples:
run `scrape`, review a sample of stored screenings, and append a case per
`GoldenCase`'s fields, with `notes` explaining the label's rationale.
