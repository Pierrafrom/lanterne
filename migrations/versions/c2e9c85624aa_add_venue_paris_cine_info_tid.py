"""add venue paris cine info tid

Paris Ciné Info's own stable theatre identifier per venue (e.g. "C0140"),
recorded from every showtime's `tid` field (see
`ParisCineInfoScraper.fetch_events` and `EventRepository._resolve_venue`).
A stronger natural key than `slug` when available: two sources describing
the same physical venue with different wording used to create two `Venue`
rows (see `docs/decisions/0012-retire-lechampo.md`'s venue-name
fragmentation follow-up) — this column lets them be recognized, and merged,
as one. Nullable, no backfill: existing venues start unmatched (`None`) and
get stamped or merged lazily as Paris Ciné Info re-scrapes each venue.

Revision ID: c2e9c85624aa
Revises: 06ff4ff8133e
Create Date: 2026-07-14 16:00:16.642692

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2e9c85624aa"
down_revision: str | Sequence[str] | None = "06ff4ff8133e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("paris_cine_info_tid", sa.String(), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_venue_paris_cine_info_tid", ["paris_cine_info_tid"]
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("venue", schema=None) as batch_op:
        batch_op.drop_constraint("uq_venue_paris_cine_info_tid", type_="unique")
        batch_op.drop_column("paris_cine_info_tid")
