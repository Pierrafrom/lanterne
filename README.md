# cine-event-bot

Auto-hosted Telegram bot aggregating special screenings in Paris/IDF (avant-premières, ciné-concerts, rétrospectives, plein air), sending a weekly digest, and answering natural-language questions about programmed films via the TMDB API.

## Install & run

```bash
# Clone and install
git clone https://github.com/Pierrafrom/cine-event-bot
cd cine-event-bot
uv sync --all-groups

# Configure
cp .env.example .env
# Fill in TELEGRAM_BOT_TOKEN, TMDB_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL

# Run
uv run python -m cine_event_bot

# Or with Docker
docker compose up
```

## Test

```bash
uv run pytest
```

## Architecture & decisions

See [`CLAUDE.md`](CLAUDE.md) for the async data flow and key technical decisions.
