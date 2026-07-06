# Contributing

Thanks for contributing to cine-event-bot. This guide covers the local workflow;
see [docs/setup.md](docs/setup.md) for full install/configuration and
[docs/testing.md](docs/testing.md) for the testing approach.

## Getting set up

```fish
uv sync --all-groups        # install runtime + dev dependencies
uv run pre-commit install   # enable the commit-time lint/format/type gate
cp .env.example .env         # fill in the tokens (never commit .env)
```

## Before you push — quality gates

The pre-commit hook runs these automatically on every commit; run them by hand
to check before pushing. All must be clean (zero warnings, not just zero
errors):

```fish
uv run ruff check .    # lint
uv run ruff format .   # formatting
uv run mypy src        # strict type checking
uv run pytest          # tests + coverage (fails under 80%)
```

New behaviour ships with tests (TDD) and updated docs in the same change — a
feature is not "done" until both are in place.

## Branch and PR workflow

- **Never commit work in progress directly to `main`.** Branch first:
  `feat/<topic>`, `fix/<topic>`, `refactor/<topic>`, `docs/<topic>`.
- Keep changes atomic — one logical change per commit.
- **Commit messages** follow Conventional Commits:
  `type(scope): short description` where `type` is one of `feat`, `fix`,
  `refactor`, `test`, `docs`, `chore`, `perf`.
- Open a PR against `main`; keep it focused and reasonably small. Ensure the
  gates above are green and mention any behaviour/interface change in the
  description.

## Conventions

- **Code, comments, docstrings, commit messages, docs: English.** The only
  exception is end-user-facing Telegram text, which is French (see `CLAUDE.md`).
- Google-style docstrings on every public module, class, and function.
- Type hints on every public signature (`mypy --strict` baseline).
- Structured JSONL logging via `get_logger(__name__)` — never `print()`.
- A new scraped source plugs into the `SourceScraper` protocol and the
  `build_scrapers` registry; follow the decision tree in
  [docs/scraping-strategy.md](docs/scraping-strategy.md).

## Architectural decisions

Significant, hard-to-reverse choices are recorded as ADRs in
[docs/decisions/](docs/decisions/). Add a new numbered ADR when you make one
rather than burying the rationale in a commit message.
