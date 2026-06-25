# Multi-stage build — runtime image contains only what the bot needs at runtime
FROM python:3.11-slim AS base

# uv binary from its official image — no pip bootstrap needed
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies first for better layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ ./src/

# Non-root user (DevSecOps baseline — see rules/devops/devops.md)
RUN adduser --disabled-password --gecos "" botuser
USER botuser

# Logs mounted as a volume from docker-compose, not baked into the image
ENV LOG_LEVEL=INFO
ENV PYTHONUNBUFFERED=1

CMD ["uv", "run", "python", "-m", "cine_event_bot"]
