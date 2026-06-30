"""Domain models for special screenings, LLM extraction, and subscribers.

Three concerns are deliberately kept in separate types (see
``docs/decisions/0001-event-model-split.md``):

- :class:`ExtractedEvent` — the structured output contract for the LLM, holding
  only what can be read from a scraped announcement.
- :class:`ScreeningEvent` — the persisted table row, adding provenance, the
  deduplication key, and optional TMDB enrichment filled in later.
- :class:`Subscriber` — a Telegram chat opted in to the weekly digest.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, field_validator
from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

from cine_event_bot.core.dedup import compute_dedup_key


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
    """Category of special screening tracked by the bot."""

    AVANT_PREMIERE = "avant_premiere"
    CINE_CONCERT = "cine_concert"
    RETROSPECTIVE = "retrospective"
    OPEN_AIR = "open_air"


class Source(StrEnum):
    """Scraped source a screening was discovered on; value is its domain."""

    PREMIERE_PROJO = "premiereprojo.fr"
    CINEMATHEQUE = "cinematheque.fr"
    FORUM_DES_IMAGES = "forumdesimages.fr"


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
    """

    title: str
    event_type: EventType
    venue: str
    starts_at: datetime
    has_team_present: bool = False
    description: str | None = None

    @field_validator("has_team_present", mode="before")
    @classmethod
    def _default_team_presence(cls, value: Any) -> Any:  # noqa: ANN401 — pre-validation hook
        """Treat a missing/null team flag as ``False`` (LLMs often emit null)."""
        return False if value is None else value


class ScreeningEvent(SQLModel, table=True):
    """A deduplicated special screening as stored in the database.

    Built from an :class:`ExtractedEvent` plus its provenance via
    :meth:`from_extracted`. TMDB enrichment fields stay ``None`` until the
    enrichment step fills them.

    Attributes:
        id: Surrogate primary key.
        dedup_key: Unique key identifying the screening across sources.
        source: Source the screening was discovered on.
        source_url: Direct link to the announcement, when available.
        tmdb_id: TMDB identifier of the matched film, once enriched.
        overview: TMDB synopsis, once enriched.
        poster_url: TMDB poster URL, once enriched.
        release_year: TMDB release year, once enriched.
    """

    id: int | None = Field(default=None, primary_key=True)
    dedup_key: str = Field(unique=True, index=True)

    title: str
    event_type: EventType
    venue: str
    starts_at: datetime = Field(sa_type=UtcDateTime)
    has_team_present: bool = False
    description: str | None = None

    source: Source
    source_url: str | None = None

    tmdb_id: int | None = None
    overview: str | None = None
    poster_url: str | None = None
    release_year: int | None = None

    @classmethod
    def from_extracted(
        cls,
        extracted: ExtractedEvent,
        *,
        source: Source,
        source_url: str | None,
    ) -> "ScreeningEvent":
        """Build a persistable event from an extracted announcement.

        Computes the deduplication key from the screening's intrinsic identity
        and copies the extracted fields verbatim. TMDB enrichment is left empty.

        Args:
            extracted: Structured data read from a scraped announcement.
            source: Source the announcement came from.
            source_url: Direct link to the announcement, if known.

        Returns:
            A :class:`ScreeningEvent` ready to be persisted (no ``id`` yet).
        """
        return cls(
            dedup_key=compute_dedup_key(
                extracted.title, extracted.venue, extracted.starts_at
            ),
            title=extracted.title,
            event_type=extracted.event_type,
            venue=extracted.venue,
            starts_at=extracted.starts_at,
            has_team_present=extracted.has_team_present,
            description=extracted.description,
            source=source,
            source_url=source_url,
        )


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
