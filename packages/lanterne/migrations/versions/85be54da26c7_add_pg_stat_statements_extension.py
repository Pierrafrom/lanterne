"""add pg_stat_statements extension

Revision ID: 85be54da26c7
Revises: 3572bbb55a5f
Create Date: 2026-07-16 23:28:52.584574

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "85be54da26c7"
down_revision: str | Sequence[str] | None = "3572bbb55a5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add pg_stat_statements for query-level performance observability.

    Requires ``shared_preload_libraries=pg_stat_statements`` at server
    start (set on the ``postgres`` service in ``docker-compose.yml`` and
    the test suite's container in ``tests/conftest.py``) — ``CREATE
    EXTENSION`` alone raises if the library wasn't preloaded. See
    ``docs/decisions/0022-postgres-extensions.md``.
    """
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")


def downgrade() -> None:
    """Drop pg_stat_statements."""
    op.execute("DROP EXTENSION IF EXISTS pg_stat_statements")
