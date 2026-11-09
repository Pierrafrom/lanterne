# Multi-stage build — same uv workspace pattern as docker/lanterne.Dockerfile
# (docs.astral.sh/uv/guides/integration/docker/): third-party deps first,
# then this member's own source, via --package so services/web isn't needed
# in this image's build context.
FROM python:3.11-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages/lanterne/pyproject.toml,target=packages/lanterne/pyproject.toml \
    --mount=type=bind,source=services/api/pyproject.toml,target=services/api/pyproject.toml \
    uv sync --frozen --no-install-workspace --no-dev

COPY pyproject.toml uv.lock ./
COPY packages/lanterne/pyproject.toml packages/lanterne/pyproject.toml
COPY packages/lanterne/src/ packages/lanterne/src/
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/api/src/ services/api/src/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package lanterne-api

RUN adduser --disabled-password --gecos "" apiuser
USER apiuser

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--app-dir", "services/api/src", "--host", "0.0.0.0", "--port", "8000"]
