"""Domain models for special screenings, LLM extraction, and subscribers.

The extraction contract and the persisted schema are deliberately separate
types (see ``docs/decisions/0001-event-model-split.md``), and the persisted
schema is normalized around the screening (see
``docs/decisions/0006-relational-schema-split.md``):

- :class:`ExtractedEvent` — the structured output contract for the LLM, holding
  only what can be read from a scraped announcement.
- :class:`Sighting` — one announcement as observed on one source: the extracted
  facts plus their provenance, ready for ingestion.
- :class:`Film` — one row per film, shared by all its screenings; carries the
  TMDB enrichment.
- :class:`Venue` — one row per venue, keyed by its normalized name.
- :class:`ScreeningEvent` — the persisted screening, referencing its film and
  venue and holding the deduplication key.
- :class:`EventSighting` — one (event, source) provenance row, recording every
  source that reported a screening.
- :class:`Subscriber` — a Telegram chat opted in to the weekly digest.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import JSON, DateTime, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, Relationship, SQLModel


class UtcDateTime(TypeDecorator[datetime]):
    """Persist timezone-aware datetimes as UTC and read them back as UTC.

    SQLite does not preserve timezone info, so a stored aware-UTC datetime would
    otherwise come back naive and be misread as server-local time. This
    normalizes to UTC on write and re-attaches UTC on read, keeping the database
    self-describing regardless of the backend.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,  # noqa: ARG002 — required by the TypeDecorator API
    ) -> datetime | None:
        """Normalize a value to UTC before storing it."""
        if value is None:
            return None
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC)

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Dialect,  # noqa: ARG002 — required by the TypeDecorator API
    ) -> datetime | None:
        """Return a stored value as a UTC-aware datetime."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class EventType(StrEnum):
    """Category of special screening tracked by the bot.

    Stored as plain text in SQLite, so adding a member needs no migration;
    the extraction and Q&A prompts and the French labels are generated from
    this enum (guarded by tests), so they follow automatically.
    """

    AVANT_PREMIERE = "avant_premiere"
    CINE_CONCERT = "cine_concert"
    RETROSPECTIVE = "retrospective"
    OPEN_AIR = "open_air"
    FESTIVAL = "festival"
    SEANCE_CULTE = "seance_culte"
    CINE_CLUB = "cine_club"
    COURT_METRAGE = "court_metrage"


class Source(StrEnum):
    """Scraped source a screening was discovered on; value is its domain."""

    PREMIERE_PROJO = "premiereprojo.fr"
    CINEMATHEQUE = "cinematheque.fr"
    FORUM_DES_IMAGES = "forumdesimages.fr"
    PARIS_CINE_INFO = "paris-cine.info"
    LE_CHAMPO = "cinema-lechampo.com"
    LE_LOUXOR = "cinemalouxor.fr"


class ExtractedEvent(BaseModel):
    """Structured screening data read from a single scraped announcement.

    This is the Pydantic contract the LLM is asked to fill. It holds nothing
    about provenance or enrichment — only the facts present in the source text.

    Attributes:
        title: Film title as announced.
        event_type: Category of the special screening.
        venue: Cinema or venue hosting the screening.
        starts_at: Screening start time, timezone-aware UTC.
        has_team_present: Whether the film team attends (priority signal for
            avant-premières).
        description: Free-text details when the source provides them.
        cycle_name: Retrospective/festival cycle the screening belongs to, when
            the source announces one.
    """

    title: str
    event_type: EventType
    venue: str
    starts_at: datetime
    has_team_present: bool = False
    description: str | None = None
    cycle_name: str | None = None

    @field_validator("has_team_present", mode="before")
    @classmethod
    def _default_team_presence(cls, value: Any) -> Any:  # noqa: ANN401 — pre-validation hook
        """Treat a missing/null team flag as ``False`` (LLMs often emit null)."""
        return False if value is None else value


class Sighting(BaseModel):
    """One screening announcement as observed on one source.

    This is what scrapers produce: the extracted facts plus their provenance.
    It is deliberately not a table — the repository resolves it into
    :class:`Film`, :class:`Venue`, and :class:`ScreeningEvent` rows at
    ingestion time.

    Attributes:
        extracted: The structured facts read from the announcement.
        source: Source the announcement was observed on.
        source_url: Direct link to the announcement, when available.
        booking_url: Direct link to buy tickets, when the source provides one.
    """

    model_config = ConfigDict(frozen=True)

    extracted: ExtractedEvent
    source: Source
    source_url: str | None = None
    booking_url: str | None = None


class Film(SQLModel, table=True):
    """A film, shared by every screening that shows it.

    One row per film: created from the announced title at ingestion, then
    enriched from TMDB once (all screenings of the film share the enrichment).

    Attributes:
        id: Surrogate primary key.
        title_key: Normalized announced title, the natural key before a TMDB
            match exists.
        title: Film title as first announced (display form).
        tmdb_id: TMDB identifier, once matched.
        original_title: Original-language title, once enriched.
        director: Director name(s), once enriched.
        release_year: Release year, once enriched.
        runtime_minutes: Runtime in minutes, once enriched.
        genres: Genre names, once enriched.
        overview: Synopsis, once enriched.
        poster_url: Absolute poster URL, once enriched.
        vote_average: TMDB rating (0–10), once enriched.
    """

    id: int | None = Field(default=None, primary_key=True)
    title_key: str = Field(unique=True, index=True)
    title: str

    tmdb_id: int | None = Field(default=None, unique=True)
    original_title: str | None = None
    director: str | None = None
    release_year: int | None = None
    runtime_minutes: int | None = None
    genres: list[str] | None = Field(default=None, sa_type=JSON)
    overview: str | None = None
    poster_url: str | None = None
    vote_average: float | None = None

    screenings: list["ScreeningEvent"] = Relationship(back_populates="film")


class Venue(SQLModel, table=True):
    """A cinema or venue hosting screenings.

    One row per venue, keyed by the normalized announced name so every source's
    casing/spacing variant resolves to the same row.

    Attributes:
        id: Surrogate primary key.
        slug: Normalized venue name, the natural key.
        name: Venue name as first announced (display form).
        address: Street address, once enriched.
        website: Official website URL, once enriched.
    """

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    address: str | None = None
    website: str | None = None

    screenings: list["ScreeningEvent"] = Relationship(back_populates="venue")


class ScreeningEvent(SQLModel, table=True):
    """A deduplicated special screening as stored in the database.

    Built by the repository from a :class:`Sighting` (see
    ``EventRepository.ingest``), which resolves the film and venue rows and
    computes the deduplication key.

    Attributes:
        id: Surrogate primary key.
        dedup_key: Unique key identifying the screening across sources.
        film_id: The screened film.
        venue_id: The hosting venue.
        event_type: Category of the special screening.
        starts_at: Screening start time, timezone-aware UTC.
        has_team_present: Whether the film team attends.
        description: Free-text details from the source, when provided.
        cycle_name: Retrospective/festival cycle, when announced.
        booking_url: Ticket-purchase link, when provided.
    """

    id: int | None = Field(default=None, primary_key=True)
    dedup_key: str = Field(unique=True, index=True)

    film_id: int = Field(foreign_key="film.id", index=True)
    venue_id: int = Field(foreign_key="venue.id", index=True)

    event_type: EventType
    starts_at: datetime = Field(sa_type=UtcDateTime)
    has_team_present: bool = False
    description: str | None = None
    cycle_name: str | None = None
    booking_url: str | None = None

    film: Film = Relationship(back_populates="screenings")
    venue: Venue = Relationship(back_populates="screenings")


class EventSighting(SQLModel, table=True):
    """One source's report of a screening — full cross-source provenance.

    One row per (event, source) pair: every source that announced a screening
    keeps its own link and timestamp, instead of only the first one surviving
    (see ``docs/decisions/0006-relational-schema-split.md``, superseding that
    consequence of ADR 0002).

    Attributes:
        id: Surrogate primary key.
        event_id: The screening this sighting reports.
        source: Source the announcement was observed on.
        source_url: Direct link to the announcement, when available.
        scraped_at: When the sighting was recorded, timezone-aware UTC.
    """

    __table_args__ = (UniqueConstraint("event_id", "source"),)

    id: int | None = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="screeningevent.id", index=True)
    source: Source
    source_url: str | None = None
    scraped_at: datetime = Field(sa_type=UtcDateTime)


class Subscriber(SQLModel, table=True):
    """A Telegram chat opted in to receive the weekly digest.

    Attributes:
        chat_id: Telegram chat identifier (primary key).
        subscribed_at: When the chat opted in, timezone-aware UTC.
        is_active: Whether the chat currently receives digests; set to ``False``
            on ``/stop`` rather than deleting the row, to keep history.
    """

    chat_id: int = Field(primary_key=True)
    subscribed_at: datetime = Field(sa_type=UtcDateTime)
    is_active: bool = True
