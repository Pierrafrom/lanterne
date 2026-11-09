"""replace venue lat lon with postgis geography

Revision ID: a421d67e9c58
Revises: 85be54da26c7
Create Date: 2026-07-16 23:41:07.644527

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography

# revision identifiers, used by Alembic.
revision: str = "a421d67e9c58"
down_revision: str | Sequence[str] | None = "85be54da26c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace venue.latitude/longitude with a single PostGIS geography point.

    Not a pure structural migration — existing geocoded venues (see
    ``docs/decisions/0021-venue-screen-split-and-geocoding.md``) have their
    coordinates copied into the new ``location`` column before the old ones
    are dropped. See ``docs/decisions/0022-postgres-extensions.md`` for why
    this replaces the two raw float columns rather than living alongside
    them.
    """
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.add_column(
        "venue",
        sa.Column(
            "location",
            Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE venue
        SET location = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        """
    )
    op.create_index("ix_venue_location", "venue", ["location"], postgresql_using="gist")
    op.drop_column("venue", "longitude")
    op.drop_column("venue", "latitude")


def downgrade() -> None:
    """Restore latitude/longitude float columns, backfilled from location."""
    op.add_column(
        "venue", sa.Column("latitude", sa.Float(), autoincrement=False, nullable=True)
    )
    op.add_column(
        "venue",
        sa.Column("longitude", sa.Float(), autoincrement=False, nullable=True),
    )
    op.execute(
        """
        UPDATE venue
        SET latitude = ST_Y(location::geometry), longitude = ST_X(location::geometry)
        WHERE location IS NOT NULL
        """
    )
    op.drop_index("ix_venue_location", table_name="venue")
    op.drop_column("venue", "location")
    op.execute("DROP EXTENSION IF EXISTS postgis")
