"""add venue offi_slug

Revision ID: f98fb56722e4
Revises: f28833a889dc
Create Date: 2026-07-22 01:55:32.503850

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f98fb56722e4"
down_revision: str | Sequence[str] | None = "f28833a889dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add venue.offi_slug — see Venue.offi_slug's docstring for why.

    Autogenerate also proposed dropping ``spatial_ref_sys`` (a PostGIS
    system table, not part of this project's models), the two
    ``immutable_unaccent``-based trigram indexes, and the GiST index on
    ``venue.location`` — all three created via raw SQL in earlier
    migrations (see ``3572bbb55a5f`` and ``a421d67e9c58``) and never
    recognized by autogenerate's model diff; stripped here, same as every
    prior migration that touched this schema.
    """
    op.add_column(
        "venue",
        sa.Column("offi_slug", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_unique_constraint(op.f("uq_venue_offi_slug"), "venue", ["offi_slug"])


def downgrade() -> None:
    """Reverse :func:`upgrade`."""
    op.drop_constraint(op.f("uq_venue_offi_slug"), "venue", type_="unique")
    op.drop_column("venue", "offi_slug")
