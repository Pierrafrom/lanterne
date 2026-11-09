"""Public API entry point for Lanterne.

Hello-world scaffolding only — no business endpoints yet. Exists to prove
the deployment chain works end to end (API container ↔ PostgreSQL ↔ web
container), see ``docs/vision.md`` for the target shape of the real API.
Reuses ``lanterne.config.Settings`` and ``lanterne.io.db.Database`` rather
than a second settings/connection layer, per
``docs/decisions/0015-uv-workspace-monorepo.md``.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from lanterne.config import Settings
from lanterne.io.db import Database
from sqlalchemy import text


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database connection pool for the app's lifetime."""
    settings = Settings()
    database = Database(settings.database_url)
    app.state.database = database
    try:
        yield
    finally:
        await database.dispose()


app = FastAPI(title="Lanterne API", lifespan=_lifespan)


@app.get("/health")
async def health() -> Mapping[str, Any]:
    """Report service status and PostgreSQL reachability.

    Returns:
        A mapping with ``status`` (``"ok"`` or ``"error"``) and, on
        success, ``database`` set to ``"reachable"``.
    """
    database: Database = app.state.database
    try:
        async with database.session() as session:
            await session.exec(text("SELECT 1"))
    except Exception as error:  # a health check reports any failure, not a specific one
        return {"status": "error", "database": str(error)}
    return {"status": "ok", "database": "reachable"}
