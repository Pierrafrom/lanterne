# Copilot Instructions — cine-event-bot

## Project summary

Auto-hosted Telegram bot that aggregates special screenings in Paris/IDF (avant-premières with
the team, ciné-concerts, retrospectives, open-air) from four scraped sources, sends a weekly
digest, and answers natural-language questions about programmed films using TMDB data.
Personal project — solo developer, self-hosted.

## Tech stack

- **Language**: Python 3.11+ (actual runtime: 3.12)
- **Bot framework**: aiogram 3.x — async, event-loop driven
- **HTTP client**: httpx with HTTP/2 — use `AsyncClient`, never `requests`
- **HTML parsing**: beautifulsoup4 — for scraping cinema sources
- **Database**: SQLModel (SQLAlchemy 2 + Pydantic) + aiosqlite — async sessions only
- **LLM extraction**: Instructor 1.x with an Ollama-compatible OpenAI endpoint
- **Config**: pydantic-settings — env vars only, never hardcoded values
- **CLI**: Typer — admin commands; intentionally sync, wraps `asyncio.run()`
- **Tools**: ruff (lint+format+imports), mypy --strict, pytest + pytest-asyncio, uv (deps)

## Architecture: async all the way

The aiogram event loop is the runtime root. Every I/O operation must be async.

```
aiogram (event loop)
   ├── httpx.AsyncClient → TMDB API
   ├── Instructor (async) → Ollama → structured event extraction
   └── SQLModel (async session, aiosqlite) → read/write events

Weekly cron (Typer CLI → asyncio.run())
   ├── httpx.AsyncClient → scrape 4 sources
   ├── Instructor (async) → Ollama → structure + dedup events
   └── SQLModel (async session) → insert
```

**Never introduce a sync blocking call inside an `async def`** — no `requests.get()`,
no sync SQLAlchemy sessions, no `time.sleep()` in async context. Use `asyncio.sleep()`,
`httpx.AsyncClient`, and `async_session` throughout.

## Package layout

```
src/cine_event_bot/
├── main.py              # Typer admin CLI — sync entry point, calls asyncio.run()
├── logging_config.py    # JSONL structured logger — import get_logger(__name__) everywhere
├── core/                # pure business logic: domain models, digest builder, dedup
└── io/                  # external I/O only: scraper, TMDB client, aiogram bot handlers
```

- `core/` must not import from `io/` — business logic has no knowledge of transport.
- `io/` calls into `core/` for business rules, never the reverse.
- All imports are **absolute** (`from cine_event_bot.core.models import Event`), never
  relative (`from ..core.models import Event`) — enforced by ruff TID rule.

## Code conventions

### Logging — always structured JSONL, never plain `print`

```python
# Preferred — structured, filterable with grep/jq
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)
logger.error("scraping failed", extra={"ctx": {"source": "mk2.fr", "status": 404}})

# Avoid — free text, no structure, can't be filtered programmatically
print(f"scraping failed for mk2.fr with status 404")
```

### Async database sessions — always via context manager

```python
# Preferred
async with async_session() as session:
    result = await session.execute(select(Event).where(Event.is_special == True))
    events = result.scalars().all()

# Avoid — sync session, blocks the event loop
with Session(engine) as session:
    events = session.exec(select(Event)).all()
```

### Instructor for LLM extraction — structured output only

```python
# Preferred — Instructor enforces the Pydantic schema via retries
import instructor
from openai import AsyncOpenAI

client = instructor.from_openai(
    AsyncOpenAI(base_url=settings.ollama_base_url + "/v1", api_key="ollama"),
    mode=instructor.Mode.JSON,
)
event = await client.chat.completions.create(
    model=settings.ollama_model,
    response_model=ScreeningEvent,
    messages=[{"role": "user", "content": raw_html_text}],
)

# Avoid — raw JSON parsing from LLM output, no retry, no validation
response = await ollama_client.chat(...)
data = json.loads(response["message"]["content"])
```

### httpx — always use AsyncClient, never the module-level sync functions

```python
# Preferred — connection pooling, reuse across requests
async with httpx.AsyncClient(timeout=10.0) as client:
    response = await client.get(url)

# Avoid — creates a new connection every time, no pooling
response = httpx.get(url)
```

### Config via pydantic-settings — settings object, never os.environ directly

```python
# Preferred
from cine_event_bot.core.config import settings
token = settings.telegram_bot_token

# Avoid — bypasses validation, easy to miss a missing variable
import os
token = os.environ["TELEGRAM_BOT_TOKEN"]
```

### Type hints — strict mypy, modern syntax

```python
# Preferred — modern union syntax, concrete return types
async def fetch_events(source_url: str) -> list[ScreeningEvent]: ...

# Avoid — legacy typing imports, implicit Any
from typing import List, Optional
async def fetch_events(source_url) -> List:  ...
```

### Docstrings — Google-style, English, on every public function/class

```python
def get_logger(name: str) -> logging.Logger:
    """Return a configured JSONL logger.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A Logger with JSONL file and stdout handlers.
    """
```

## Tests

- **Framework**: pytest + pytest-asyncio (`asyncio_mode = "auto"` in pyproject.toml —
  `async def test_*` functions are automatically treated as asyncio tests, no decorator needed)
- **Naming**: `test_<behavior>_<condition>` (e.g. `test_scraper_returns_empty_list_on_404`)
- **Coverage target**: 80%+ on `src/` — enforced by `--cov-fail-under=80`
- **No sync blocking in async tests** — same rule as production code
- **No real network calls in unit tests** — use `respx` to mock httpx, or dependency injection

## Security

- **No hardcoded secrets** — `TELEGRAM_BOT_TOKEN`, `TMDB_API_KEY`, `OLLAMA_BASE_URL` come
  from environment variables loaded by pydantic-settings; never appear as string literals
- **No committing `.env`** — `.gitignore` excludes it; `.env.example` documents variables
- **No user-controlled strings in SQL** — always use SQLModel/SQLAlchemy parameterized queries
