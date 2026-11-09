# Multi-stage build — runtime image contains only what the bot/scraper needs.
# Base: docs.astral.sh/uv/guides/integration/docker/ — uv workspace pattern:
# third-party deps first (--no-install-workspace, cached independently of
# application code), then the `lanterne` member itself via --package, so
# adding services/api as a second workspace member later doesn't require
# copying its files into this image.
FROM python:3.11-slim AS base

# uv binary from its official image — no pip bootstrap needed
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# postgresql-client-18 (pg_dump) — used by `backup-db`, see io/db.py::backup_to.
# Debian's own default repo only ships whichever major version shipped with
# the Debian release (17 for this base image), which pg_dump refuses to run
# against an 18.x server ("aborting because of server version mismatch") —
# confirmed live. The PGDG apt repo's official automated setup script
# (postgresql.org/download/linux/debian) installs the matching major
# version instead. A system package, not a Python dependency, so it's
# installed here rather than through uv.
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-common \
    && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/lanterne/pyproject.toml,target=packages/lanterne/pyproject.toml \
    uv sync --frozen --no-install-workspace --no-dev

COPY pyproject.toml uv.lock ./
COPY packages/lanterne/pyproject.toml packages/lanterne/pyproject.toml
COPY packages/lanterne/src/ packages/lanterne/src/
COPY packages/lanterne/alembic.ini packages/lanterne/alembic.ini
COPY packages/lanterne/migrations/ packages/lanterne/migrations/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package lanterne

# Non-root user (DevSecOps baseline — see rules/devops/devops.md). logs/ and
# backups/ are created here (not left for the app to mkdir at runtime) and
# owned by botuser, so both a plain `docker run` and a docker-compose volume
# mount onto an empty/root-owned host directory end up writable.
RUN adduser --disabled-password --gecos "" botuser \
    && mkdir -p logs backups \
    && chown -R botuser:botuser /app
USER botuser

# Run the already-synced venv directly rather than through `uv run` — `uv
# run` re-syncs on every invocation by default (including the dev group),
# which both wastes time and fails outright as a non-root user against a
# venv built while root (see docs.astral.sh/uv/guides/integration/docker/).
ENV PATH="/app/.venv/bin:$PATH"

# Logs/backups mounted as volumes from docker-compose, not baked into the
# image — written relative to WORKDIR (/app), matching those mount targets.
ENV LOG_LEVEL=INFO
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "lanterne"]
