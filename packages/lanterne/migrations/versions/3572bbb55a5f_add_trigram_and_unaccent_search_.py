"""add trigram and unaccent search extensions

Revision ID: 3572bbb55a5f
Revises: 7868904a30b8
Create Date: 2026-07-16 19:08:24.550214

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3572bbb55a5f"
down_revision: str | Sequence[str] | None = "7868904a30b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# unaccent() is STABLE, not IMMUTABLE (Postgres can't prove the text search
# dictionary it reads never changes) — a functional index requires an
# IMMUTABLE wrapper. This is the standard, widely-used pattern for
# accent-insensitive trigram search; see
# docs/decisions/0022-postgres-extensions.md.
#
# Uses the SQL-standard function body (``RETURN expr``, PG14+), not the
# older ``AS $$ SELECT ... $$`` text-body form — confirmed live that the
# older form breaks ``CREATE INDEX`` on an expression calling this function:
# Postgres defers resolving ``unaccent`` inside a text-body function until
# first use, and that deferred resolution fails specifically during
# ``CREATE INDEX``'s expression analysis (``function unaccent(text) does not
# exist``), even though calling the same function via a plain ``SELECT``
# works fine and even across separate, committed transactions/connections.
# The SQL-standard body resolves ``unaccent`` immediately at CREATE FUNCTION
# time instead, sidestepping the issue entirely. The single-argument form of
# unaccent() already defaults to the "unaccent" dictionary — no explicit
# regdictionary argument needed.
_CREATE_IMMUTABLE_UNACCENT = """
    CREATE OR REPLACE FUNCTION immutable_unaccent(input text)
    RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
    RETURN unaccent(input)
"""


def upgrade() -> None:
    """Add pg_trgm/unaccent and a trigram index on Film.title/Film.overview."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(_CREATE_IMMUTABLE_UNACCENT)
    op.execute(
        "CREATE INDEX ix_film_title_trgm ON film "
        "USING gin (immutable_unaccent(title) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_film_overview_trgm ON film "
        "USING gin (immutable_unaccent(overview) gin_trgm_ops)"
    )


def downgrade() -> None:
    """Drop the trigram indexes, wrapper function, and extensions."""
    op.execute("DROP INDEX IF EXISTS ix_film_overview_trgm")
    op.execute("DROP INDEX IF EXISTS ix_film_title_trgm")
    op.execute("DROP FUNCTION IF EXISTS immutable_unaccent(text)")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
