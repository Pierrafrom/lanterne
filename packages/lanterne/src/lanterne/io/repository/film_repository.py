"""Read/write access to films: resolution, TMDB matching, and ratings.

Split out of the former monolithic ``EventRepository`` (see
``docs/architecture.md``) — every method here concerns a :class:`Film` row
in isolation, with no knowledge of screenings or venues.
"""

from datetime import UTC, datetime

from sqlalchemy import delete, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from lanterne.core.dedup import normalize_text
from lanterne.core.models import Film, FilmRating, ScreeningEvent
from lanterne.io.repository._commit import commit
from lanterne.io.scrapers.base import RatingRecord


class FilmRepository:
    """Read/write access to persisted films."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to an active session.

        Args:
            session: Active async session bound to the target database.
        """
        self._session = session

    async def resolve(self, title: str) -> Film:
        """Return the film row for an announced title, creating it if new.

        Args:
            title: Film title as announced by the source.

        Returns:
            The resolved (possibly newly created, unsaved) film row.
        """
        title_key = normalize_text(title)
        statement = select(Film).where(Film.title_key == title_key)
        result = await self._session.exec(statement)
        existing = result.first()
        if existing is not None:
            return existing
        return Film(title_key=title_key, title=title)

    async def list_film_ratings(self, film_id: int) -> list[FilmRating]:
        """Return every external site's rating of a film.

        Args:
            film_id: Primary key of the film.

        Returns:
            The film's ratings, one per source that carried one (see
            ``docs/decisions/0013-film-ratings-from-paris-cine-info.md``).
        """
        statement = select(FilmRating).where(FilmRating.film_id == film_id)
        result = await self._session.exec(statement)
        return list(result.all())

    async def list_films_missing_imdb_id(self) -> list[Film]:
        """Return every TMDB-enriched film still missing an IMDb id.

        Used by the one-off ``backfill-ratings`` CLI command to catch up
        films enriched before ``Film.imdb_id``/``backdrop_url`` existed
        (see ``docs/decisions/0013-film-ratings-from-paris-cine-info.md``)
        — the normal enrichment path only ever touches a film once
        (``IngestionPipeline._enrich``'s ``tmdb_id is not None`` guard), so
        these are otherwise never revisited.

        Returns:
            Every film with a TMDB match but no IMDb id yet.
        """
        statement = select(Film).where(
            col(Film.tmdb_id).is_not(None), col(Film.imdb_id).is_(None)
        )
        result = await self._session.exec(statement)
        return list(result.all())

    async def save_film(self, film: Film) -> None:
        """Persist in-place changes to a film (e.g. after TMDB enrichment).

        Args:
            film: The film row to save.
        """
        self._session.add(film)
        await commit(self._session)

    async def find_film_by_tmdb_id(self, tmdb_id: int) -> Film | None:
        """Return the film row already matched to a TMDB id, if any.

        Used by ``pipeline.py::IngestionPipeline._enrich`` to detect a
        duplicate film row before saving a fresh TMDB match — two
        differently-worded announced titles for the same real film (a
        punctuation variant ``core/dedup.py::normalize_text`` does not
        unify) resolve to the same ``tmdb_id`` but started as two different
        ``Film`` rows. Called right after a caller sets an in-memory
        ``film.tmdb_id`` it has not saved yet, so autoflush is suspended for
        this query — otherwise SQLAlchemy would flush that pending change
        first and raise the very collision this method exists to detect
        before it, instead of returning the existing row cleanly.

        Args:
            tmdb_id: The TMDB identifier to look up.

        Returns:
            The film row already holding this ``tmdb_id``, or ``None``.
        """
        statement = select(Film).where(Film.tmdb_id == tmdb_id)
        with self._session.no_autoflush:
            result = await self._session.exec(statement)
            return result.first()

    async def merge_film(self, loser: Film, winner: Film) -> None:
        """Repoint every screening from a duplicate film row onto the canonical one.

        Called when two announced titles turn out to be the same TMDB film:
        ``winner`` already legitimately owns the ``tmdb_id``, so ``loser``
        (still unenriched — its own TMDB match was never saved) is retired
        rather than left as a dead-end duplicate with no metadata. Bulk SQL
        statements, not per-row ORM loads — ``loser`` may already have many
        screenings attached.

        Args:
            loser: The duplicate film row to retire; deleted by this call.
            winner: The film row every screening should point to instead.
        """
        loser_id, winner_id = loser.id, winner.id
        # loser still carries an unsaved, colliding tmdb_id in memory (the
        # enrichment that revealed the duplicate never got to save it) — drop
        # it from the session's unit of work so committing the bulk
        # statements below never tries to flush that pending write too.
        self._session.expunge(loser)
        await self._session.exec(
            update(ScreeningEvent)
            .where(col(ScreeningEvent.film_id) == loser_id)
            .values(film_id=winner_id)
        )
        await self._session.exec(delete(Film).where(col(Film.id) == loser_id))
        await commit(self._session)

    async def update_film_ratings(self, ratings: dict[str, list[RatingRecord]]) -> None:
        """Upsert already-enriched films' ratings, matched by IMDb id.

        Only enriches films that already carry a ``Film.imdb_id`` from TMDB
        enrichment — never creates a ``Film`` row from this data alone,
        same scope rule as
        ``VenueRepository.update_venue_passes`` (see
        ``pipeline.py::IngestionPipeline._apply_film_ratings`` and
        ``ParisCineInfoScraper.fetch_film_ratings``).

        Args:
            ratings: A mapping of IMDb id to that film's freshly-fetched
                :class:`~lanterne.io.scrapers.base.RatingRecord`
                list.
        """
        changed = False
        for imdb_id, records in ratings.items():
            film = await self._find_film_by_imdb_id(imdb_id)
            if film is None or film.id is None:
                continue
            for record in records:
                if await self._upsert_film_rating(film.id, record):
                    changed = True
        if changed:
            await commit(self._session)

    async def _find_film_by_imdb_id(self, imdb_id: str) -> Film | None:
        """Return the film row matched to an IMDb id, or None."""
        statement = select(Film).where(Film.imdb_id == imdb_id)
        result = await self._session.exec(statement)
        return result.first()

    async def _upsert_film_rating(self, film_id: int, record: RatingRecord) -> bool:
        """Insert or refresh one film's rating for one source.

        Returns:
            Whether the stored row was created or changed.
        """
        statement = select(FilmRating).where(
            FilmRating.film_id == film_id, FilmRating.source == record.source
        )
        result = await self._session.exec(statement)
        existing = result.first()
        now = datetime.now(UTC)
        if existing is None:
            self._session.add(
                FilmRating(
                    film_id=film_id,
                    source=record.source,
                    rating=record.rating,
                    url=record.url,
                    fetched_at=now,
                )
            )
            return True
        if existing.rating == record.rating and existing.url == record.url:
            return False
        existing.rating = record.rating
        existing.url = record.url
        existing.fetched_at = now
        self._session.add(existing)
        return True
