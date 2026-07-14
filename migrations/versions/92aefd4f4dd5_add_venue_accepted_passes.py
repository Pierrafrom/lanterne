"""add venue accepted_passes

Subscription card acceptance (UGC Illimité, Pathé CinéPass, ...) per venue,
sourced from Paris Ciné Info's authenticated ``cineOptions`` catalogue (see
``ParisCineInfoScraper.fetch_venue_passes`` and
``EventRepository.update_venue_passes``). Nullable, no backfill: existing
venues start unenriched (``None``), not "accepts no pass".

Revision ID: 92aefd4f4dd5
Revises: 0edf9c163929
Create Date: 2026-07-14 01:05:27.501169

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "92aefd4f4dd5"
down_revision: str | Sequence[str] | None = "0edf9c163929"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.add_column(sa.Column("accepted_passes", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.drop_column("accepted_passes")
