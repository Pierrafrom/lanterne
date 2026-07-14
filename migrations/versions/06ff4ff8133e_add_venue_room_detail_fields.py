"""add venue room detail fields

Room-level detail (seat count, screen dimensions) per venue, sourced from
Paris Ciné Info's per-room ``get_pcitheatre.php`` endpoint (see
``ParisCineInfoScraper.fetch_venue_details`` and
``EventRepository.update_venue_details``). Nullable, no backfill: existing
venues start unenriched (``None``), and a venue seen with more than one
distinct room in a run is deliberately left ``None`` here too — this
codebase models one ``Venue`` row per name, not per physical room, so a
multi-room venue has no single unambiguous seat count/screen size to store.

Revision ID: 06ff4ff8133e
Revises: 92aefd4f4dd5
Create Date: 2026-07-14 15:15:27.459106

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "06ff4ff8133e"
down_revision: str | Sequence[str] | None = "92aefd4f4dd5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.add_column(sa.Column("seat_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("screen_width_m", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("screen_height_m", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.drop_column("screen_height_m")
        batch_op.drop_column("screen_width_m")
        batch_op.drop_column("seat_count")
