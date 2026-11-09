"""fix jsonb none as null artifact

Revision ID: f28833a889dc
Revises: a421d67e9c58
Create Date: 2026-07-17 00:22:01.025448

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f28833a889dc"
down_revision: str | Sequence[str] | None = "a421d67e9c58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) pairs affected by the `sa_type=JSONB` (missing
# `none_as_null=True`) bug fixed alongside this migration — see
# docs/decisions/0023-jsonb-none-as-null-bug.md. Every one of these columns
# stored the JSON literal `null` for "no value known" instead of a true SQL
# NULL, silently breaking any `col IS NULL` filter.
_AFFECTED_COLUMNS = (
    ("venue", "accepted_passes"),
    ("film", "genres"),
    ("screeningevent", "specialness_reasons"),
)


def upgrade() -> None:
    """Convert the JSON `null` literal artifact back to true SQL NULL."""
    for table, column in _AFFECTED_COLUMNS:
        op.execute(
            f"UPDATE {table} SET {column} = NULL "  # noqa: S608 — fixed allowlist, no user input
            f"WHERE {column} = 'null'::jsonb"
        )


def downgrade() -> None:
    """No-op — SQL NULL and the JSON `null` literal are equally valid "no
    value known" states; there is nothing meaningful to restore.
    """
