"""add film backdrop, imdb id, and film ratings

``Film.backdrop_url``/``imdb_id`` come from TMDB (native fields on the
existing movie-details response, see ``io/tmdb.py``). ``FilmRating`` holds
one row per (film, external rating source) — IMDb, Allociné (press and
audience), SensCritique, Rotten Tomatoes, Metacritic, and Letterboxd —
sourced from Paris Ciné Info's authenticated film catalogue rather than
querying those sites directly (see
``docs/decisions/0013-film-ratings-from-paris-cine-info.md`` and
``ParisCineInfoScraper.fetch_film_ratings``). Both nullable, no backfill:
existing films start unenriched.

Revision ID: 96d1f199411a
Revises: c2e9c85624aa
Create Date: 2026-07-15 03:04:32.831749

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

import cine_event_bot.core.models

# revision identifiers, used by Alembic.
revision: str = "96d1f199411a"
down_revision: str | Sequence[str] | None = "c2e9c85624aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "filmrating",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("film_id", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "IMDB",
                "ALLOCINE_PRESS",
                "ALLOCINE_AUDIENCE",
                "SENSCRITIQUE",
                "ROTTEN_TOMATOES",
                "METACRITIC",
                "LETTERBOXD",
                name="ratingsource",
            ),
            nullable=False,
        ),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            "fetched_at",
            cine_event_bot.core.models.UtcDateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["film_id"], ["film.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("film_id", "source"),
    )
    with op.batch_alter_table("filmrating", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_filmrating_film_id"), ["film_id"], unique=False
        )

    with op.batch_alter_table("film", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("imdb_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("backdrop_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.create_index(batch_op.f("ix_film_imdb_id"), ["imdb_id"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("film", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_film_imdb_id"))
        batch_op.drop_column("backdrop_url")
        batch_op.drop_column("imdb_id")

    with op.batch_alter_table("filmrating", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_filmrating_film_id"))

    op.drop_table("filmrating")
