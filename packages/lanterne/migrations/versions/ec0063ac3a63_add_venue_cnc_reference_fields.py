"""add venue cnc reference fields

Revision ID: ec0063ac3a63
Revises: f98fb56722e4
Create Date: 2026-08-03 16:59:08.803269

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ec0063ac3a63"
down_revision: str | Sequence[str] | None = "f98fb56722e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add venue.cnc_id and its accompanying CNC-referential fields.

    See ``Venue``'s docstring in ``core/models.py`` and
    ``docs/decisions/0026-official-reference-data-layer.md``.

    Autogenerate also proposed dropping ``spatial_ref_sys`` (a PostGIS
    system table, not part of this project's models), the two
    ``immutable_unaccent``-based trigram indexes, and the GiST index on
    ``venue.location`` — all three created via raw SQL in earlier
    migrations and never recognized by autogenerate's model diff; stripped
    here, same as every prior migration that touched this schema.
    """
    op.add_column(
        "venue", sa.Column("cnc_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    op.add_column(
        "venue",
        sa.Column("insee_code", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column("venue", sa.Column("screen_count", sa.Integer(), nullable=True))
    op.add_column("venue", sa.Column("seat_count", sa.Integer(), nullable=True))
    op.add_column("venue", sa.Column("art_et_essai", sa.Boolean(), nullable=True))
    op.add_column(
        "venue",
        sa.Column(
            "art_et_essai_grade",
            sa.Enum("A", "B", "C", name="artetessaigrade", native_enum=False),
            nullable=True,
        ),
    )
    op.add_column(
        "venue",
        sa.Column(
            "art_et_essai_labels",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column("venue", sa.Column("is_multiplex", sa.Boolean(), nullable=True))
    op.add_column("venue", sa.Column("is_active", sa.Boolean(), nullable=True))
    op.create_index(op.f("ix_venue_cnc_id"), "venue", ["cnc_id"], unique=True)


def downgrade() -> None:
    """Reverse :func:`upgrade`."""
    op.drop_index(op.f("ix_venue_cnc_id"), table_name="venue")
    op.drop_column("venue", "is_active")
    op.drop_column("venue", "is_multiplex")
    op.drop_column("venue", "art_et_essai_labels")
    op.drop_column("venue", "art_et_essai_grade")
    op.drop_column("venue", "art_et_essai")
    op.drop_column("venue", "seat_count")
    op.drop_column("venue", "screen_count")
    op.drop_column("venue", "insee_code")
    op.drop_column("venue", "cnc_id")
