"""Domain models for cinema screenings, LLM extraction, and subscribers.

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
- :class:`FilmRating` — one row per (film, external rating source), sourced
  from Paris Ciné Info's authenticated film catalogue (see
  ``docs/decisions/0013-film-ratings-from-paris-cine-info.md``).
- :class:`Venue` — one row per physical cinema, keyed by its normalized name.
- :class:`Screen` — one row per screening room within a :class:`Venue` (see
  ``docs/decisions/0021-venue-screen-split-and-geocoding.md``).
- :class:`ScreeningEvent` — the persisted screening, referencing its film and
  venue and holding the deduplication key. Every screening is stored, not
  only special ones (see ``docs/decisions/0008-drop-allocine-width-source.md``
  and ``docs/coverage-matrix.md``); ``is_special`` and ``event_type``
  (nullable — ``None`` for an ordinary screening) separate "is this worth
  surfacing" from "which kind of special screening is this".
- :class:`EventSighting` — one (event, source) provenance row, recording every
  source that reported a screening.
- :class:`Subscriber` — a Telegram chat opted in to the weekly digest.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from geoalchemy2 import Geography, WKBElement, WKTElement
from geoalchemy2.shape import to_shape
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import DateTime, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, Relationship, SQLModel

# Explicit, deterministic constraint/index names (see
# alembic.sqlalchemy.org "The Importance of Naming Constraints") — without
# this, PostgreSQL and SQLAlchemy auto-generate constraint names that can
# differ run to run, which makes Alembic autogenerate diffs unreliable and
# a future `DROP CONSTRAINT` migration ambiguous to write by hand. Must be
# set before any table below is defined, since naming applies at the point
# each constraint is attached to this metadata.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UtcDateTime(TypeDecorator[datetime]):
    """Persist datetimes as ``timestamptz`` and always read them back as UTC.

    PostgreSQL's ``timestamptz`` already stores an unambiguous instant, but
    this normalizes any naive or non-UTC input to UTC before it reaches the
    database — an explicit contract that the application always thinks in
    UTC, rather than relying on each caller to have converted correctly (or
    on the session's timezone setting).
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

    Every table column backed by this project's ``StrEnum`` types (this one,
    ``Source``, ``RatingSource``, ``VenueKind``) uses
    ``sa_type=SqlEnum(..., native_enum=False, create_constraint=False)`` —
    plain VARCHAR storage with SQLAlchemy's usual Python-Enum round-trip on
    read/write, but neither a native PostgreSQL ``ENUM`` type nor a ``CHECK``
    constraint, both of which SQLModel's own default for an Enum-typed field
    would otherwise create. Either one would turn adding a member into a
    schema migration (``ALTER TYPE ... ADD VALUE`` or a constraint rewrite).
    Explicitly opting out keeps growing an enum a code-only change: the
    extraction and Q&A prompts and the French labels are generated from this
    enum (guarded by tests), so they follow automatically.
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
    FONDATION_PATHE = "fondation-jeromeseydoux-pathe.com"
    LA_VILLETTE = "lavillette.com"
    MK2 = "mk2.com"
    OFFI = "offi.fr"


class RatingSource(StrEnum):
    """External site a film rating in :class:`FilmRating` was sourced from.

    Every value is read from Paris Ciné Info's own authenticated film
    catalogue (see ``io/scrapers/paris_cine_info.py::parse_film_ratings``
    and ``docs/decisions/0013-film-ratings-from-paris-cine-info.md``) — this
    project never queries IMDb, Allociné, SensCritique, Rotten Tomatoes, or
    Letterboxd directly.
    """

    IMDB = "imdb"
    ALLOCINE_PRESS = "allocine_press"
    ALLOCINE_AUDIENCE = "allocine_audience"
    SENSCRITIQUE = "senscritique"
    ROTTEN_TOMATOES = "rotten_tomatoes"
    METACRITIC = "metacritic"
    LETTERBOXD = "letterboxd"


class GeocodeSource(StrEnum):
    """How a :class:`Venue`'s ``latitude``/``longitude`` were derived.

    See ``docs/decisions/0021-venue-screen-split-and-geocoding.md``:
    ``ADDRESS`` geocodes an already-known street address (high confidence,
    the French government's official BAN/Géoplateforme API) — ``NAME_SEARCH``
    looks up a venue with no known address by name instead (lower
    confidence; a low-confidence match is flagged via ``Venue.needs_review``
    rather than trusted silently).
    """

    ADDRESS = "address"
    NAME_SEARCH = "name_search"


class ArtEtEssaiGrade(StrEnum):
    """CNC "Art et Essai" grade, once matched to the official referential.

    The CNC's own classification of an arthouse cinema's programming
    intensity — see
    ``docs/decisions/0026-official-reference-data-layer.md`` and
    ``io/reference/idf_venues.py`` (``categorie_art_et_essai``). Confirmed
    live against the full Île-de-France dataset (2026-08-03): values range
    ``A`` through ``E``; a non-Art-et-Essai establishment carries the
    literal string ``"NON"`` in this field rather than leaving it blank —
    ``io/reference/idf_venues.py`` maps that to ``None``, since
    ``Venue.art_et_essai`` already carries the yes/no signal. Distinct from
    ``Venue.art_et_essai_labels``, which carries finer-grained labels (e.g.
    ``"RD"`` for *Recherche et Découverte*) rather than this coarse grade.
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class VenueKind(StrEnum):
    """Broad category of a venue, used as a prior signal for specialness.

    A patrimonial institution or an independent/arthouse cinema is more
    likely to be showing something worth surfacing than a chain multiplex's
    average Tuesday-evening screening — see
    ``docs/decisions/0008-drop-allocine-width-source.md`` and the
    specialness classification pipeline (``core/specialness/``).
    """

    INSTITUTION = "institution"
    CHAIN_UGC = "chain_ugc"
    CHAIN_PATHE = "chain_pathe"
    CHAIN_MK2 = "chain_mk2"
    CHAIN_OTHER = "chain_other"
    INDEPENDENT = "independent"


class ExtractedEvent(BaseModel):
    """Structured screening data read from a single scraped announcement.

    This is the Pydantic contract the LLM is asked to fill. It holds nothing
    about provenance or enrichment — only the facts present in the source text.

    Attributes:
        title: Film title as announced.
        event_type: Category of the special screening, or ``None`` for an
            ordinary screening with no specific special category (a width
            source reporting a ordinary showtime, or a screening not yet
            classified by the specialness pipeline).
        venue: Cinema or venue hosting the screening.
        starts_at: Screening start time, timezone-aware UTC.
        has_team_present: Whether the film team attends (priority signal for
            avant-premières).
        description: Free-text details when the source provides them.
        cycle_name: Retrospective/festival cycle the screening belongs to, when
            the source announces one.
    """

    title: str
    event_type: EventType | None = None
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
        venue_external_id: A stable venue identifier from the source itself
            (e.g. Paris Ciné Info's theatre id), when the source provides
            one. Provenance data, not part of ``ExtractedEvent`` since it is
            never text the LLM extracts — used by
            ``EventRepository._resolve_venue`` to recognize the same
            physical venue across sources that describe it with different
            wording (see ``Venue.paris_cine_info_tid``). ``None`` for every
            source without such an id, which keeps their existing
            name-based venue resolution unchanged.
    """

    model_config = ConfigDict(frozen=True)

    extracted: ExtractedEvent
    source: Source
    source_url: str | None = None
    booking_url: str | None = None
    venue_external_id: str | None = None


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
        imdb_id: IMDb identifier (e.g. ``"tt0055852"``), once enriched — a
            native field on TMDB's movie-details response, used as the join
            key to Paris Ciné Info's rating catalogue (see
            ``io/repository.py::EventRepository.update_film_ratings``).
        original_title: Original-language title, once enriched.
        director: Director name(s), once enriched.
        release_year: Release year, once enriched.
        runtime_minutes: Runtime in minutes, once enriched.
        genres: Genre names, once enriched.
        overview: Synopsis, once enriched.
        poster_url: Absolute poster URL, once enriched.
        backdrop_url: Absolute backdrop (landscape hero image) URL, once
            enriched.
        vote_average: TMDB rating (0–10), once enriched.
    """

    id: int | None = Field(default=None, primary_key=True)
    title_key: str = Field(unique=True, index=True)
    title: str

    tmdb_id: int | None = Field(default=None, unique=True)
    imdb_id: str | None = Field(default=None, unique=True, index=True)
    original_title: str | None = None
    director: str | None = None
    release_year: int | None = None
    runtime_minutes: int | None = None
    # none_as_null=True: JSONB defaults to storing Python None as the JSON
    # literal `null` rather than SQL NULL, breaking a plain `IS NULL` check
    # — see docs/decisions/0023-jsonb-none-as-null-bug.md.
    genres: list[str] | None = Field(
        default=None,
        sa_type=JSONB(none_as_null=True),  # type: ignore[call-overload]
    )
    overview: str | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    vote_average: float | None = None

    screenings: list["ScreeningEvent"] = Relationship(back_populates="film")


class FilmRating(SQLModel, table=True):
    """One external site's rating of a film, sourced from Paris Ciné Info.

    One row per ``(film, source)`` pair — mirrors :class:`EventSighting`'s
    one-row-per-(event, source) shape rather than widening :class:`Film`
    with a column per rating source. See
    ``docs/decisions/0013-film-ratings-from-paris-cine-info.md``.

    Attributes:
        id: Surrogate primary key.
        film_id: The rated film.
        source: External site this rating was sourced from.
        rating: The rating value, on that source's own native scale (0–5,
            0–10, or 0–100 depending on ``source`` — see
            :class:`RatingSource`'s docstring and
            ``io/scrapers/paris_cine_info.py::parse_film_ratings``).
        url: Direct link to the film on that source, when Paris Ciné Info's
            catalogue carries one (absent for the two Allociné sources —
            it exposes no Allociné slug).
        fetched_at: When this rating was last refreshed, timezone-aware UTC.
    """

    __table_args__ = (UniqueConstraint("film_id", "source"),)

    id: int | None = Field(default=None, primary_key=True)
    film_id: int = Field(foreign_key="film.id", index=True)
    # Plain VARCHAR, not a native Postgres ENUM — see EventType's docstring.
    # sa_type accepts a configured type *instance* at runtime (mirroring
    # SQLAlchemy's own Column(type_=...)), but SQLModel's stub only declares
    # `type[Any]` — a known stub imprecision, not a real type error.
    source: RatingSource = Field(
        sa_type=SqlEnum(  # type: ignore[call-overload]
            RatingSource, native_enum=False, create_constraint=False
        )
    )
    rating: float
    url: str | None = None
    fetched_at: datetime = Field(sa_type=UtcDateTime)


class Venue(SQLModel, table=True):
    """A physical cinema hosting screenings — one row per building.

    One row per venue, keyed by the normalized announced name so every source's
    casing/spacing variant resolves to the same row. A cinema with several
    screening rooms (a multiplex, or an institution like La Cinémathèque
    française with named rooms) is still exactly one ``Venue`` row — its
    individual rooms are :class:`Screen` rows, not separate venues (see
    ``docs/decisions/0021-venue-screen-split-and-geocoding.md``). A map
    showing "one point per cinema" reads directly off this table.

    Attributes:
        id: Surrogate primary key.
        slug: Normalized venue name, the natural key.
        name: Venue name as first announced (display form).
        kind: Broad venue category, a prior signal for the specialness
            classifier (see :class:`VenueKind`).
        address: Street address, once enriched.
        location: PostGIS geography point, once geocoded — see
            :class:`GeocodeSource`. ``latitude``/``longitude`` (decimal
            degrees) are read-only properties derived from this column, not
            separate storage — see
            ``docs/decisions/0022-postgres-extensions.md``.
        geocode_source: How ``location`` was derived, once set.
        needs_review: Whether the geocoding match was low-confidence (a
            name-search hit not confirmed as a cinema-type point of
            interest) and should be checked by hand before being trusted —
            e.g. on a public map. Never silently corrected automatically.
        website: Official website URL, once enriched.
        accepted_passes: Subscription card codes this venue accepts (e.g.
            ``["ugc", "pass"]`` for UGC Illimité and Pathé CinéPass), once
            enriched from Paris Ciné Info's venue-passes catalogue (see
            ``io/scrapers/paris_cine_info.py::ParisCineInfoScraper.fetch_venue_passes``
            and ``EventRepository.update_venue_passes``). ``None`` when never
            enriched — not the same as "accepts no pass".
        paris_cine_info_tid: Paris Ciné Info's own stable theatre identifier
            for this venue, once observed (e.g. ``"C0140"``). A stronger
            natural key than ``slug`` when available — see
            ``EventRepository._resolve_venue`` and
            ``docs/decisions/0012-retire-lechampo.md``'s follow-up on venue
            -name fragmentation: two different sources' wording for the same
            physical venue used to create two ``Venue`` rows; a stable
            external id lets them be recognized (and merged) as one, the
            same role ``Film.tmdb_id`` already plays for films.
        offi_slug: offi.fr's own stable per-venue URL slug (e.g.
            ``"cinema-de-la-grange-5881"``), once observed. Same role as
            ``paris_cine_info_tid`` but for the opposite failure mode:
            confirmed live (2026-07-22), offi.fr's Île-de-France network has
            *homonym* venues — two physically distinct cinemas both
            displaying as plain "La Grange" (Roissy-en-Brie and Vaux-le-
            -Pénil; a third, "La Grange de la Tremblaye" in Bois-d'Arcy,
            happens not to collide). Without a stable id, name-based
            ``slug`` matching alone would silently conflate their
            screenings into one row — this is what ``VenueRepository.resolve``
            checks before trusting a name match, for whichever source
            (``Source.OFFI`` today) provides one.
        cnc_id: The CNC's own establishment authorization number (e.g.
            ``"55"``), once matched against the official referential (see
            ``docs/decisions/0026-official-reference-data-layer.md`` and
            ``io/reference/matching.py``). The strongest natural key of
            all — official, unlike every other identifier on this model.
        insee_code: INSEE commune code (e.g. ``"75108"``), from the
            matched official establishment.
        screen_count: Total screens the CNC reports for this
            establishment. Cinema-level, from the official referential —
            distinct from ``len(Venue.screens)``, which only counts rooms a
            scraper has individually observed; a gap between the two is a
            useful coverage signal, not a conflict to reconcile.
        seat_count: Total seats the CNC reports for this establishment.
            Same cinema-level/aggregate distinction as ``screen_count``.
        art_et_essai: Whether the CNC classifies this venue as "Art et
            Essai" (arthouse), once matched. ``None`` means not yet synced,
            not "confirmed not Art et Essai".
        art_et_essai_grade: The CNC's Art et Essai grade (A/B/C), once
            matched — see :class:`ArtEtEssaiGrade`.
        art_et_essai_labels: Finer-grained Art et Essai labels (e.g.
            ``["PR"]`` for *Patrimoine et Répertoire*, the state's own
            designation for the kind of screening this project exists to
            surface), once matched.
        is_multiplex: Whether the CNC classifies this establishment as a
            multiplex, once matched.
        is_active: Whether the establishment appears on the CNC's current
            "active establishments" list. Always ``True`` when set by a
            match (only active establishments are in that list) — ``None``
            means not yet synced, not "confirmed inactive".
    """

    # WKBElement (geoalchemy2) has no Pydantic-native schema — arbitrary_types_allowed
    # lets Pydantic accept it via isinstance checks instead of trying to build
    # a full validation schema for it. Safe here: `location` is never set from
    # untrusted external input, only via VenueRepository.save_geocoding.
    # The ignore below is needed because SQLModel's stub types model_config
    # as its own private SQLModelConfig, though a plain ConfigDict works fine
    # at runtime.
    model_config = ConfigDict(arbitrary_types_allowed=True)  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    # sa_type accepts a configured type instance at runtime — see FilmRating.source.
    kind: VenueKind = Field(
        default=VenueKind.INDEPENDENT,
        sa_type=SqlEnum(  # type: ignore[call-overload]
            VenueKind, native_enum=False, create_constraint=False
        ),
    )
    address: str | None = None
    # Reading a persisted row yields a WKBElement; writing a new value (see
    # VenueRepository.save_geocoding) constructs a WKTElement instead — both
    # are valid depending on which side of the round-trip you're on.
    # spatial_index=False: the GiST index is created explicitly by the
    # migration (op.create_index), not relied on implicitly — Alembic's
    # op.add_column doesn't reliably fire geoalchemy2's own DDL-event-based
    # automatic index creation, unlike a plain metadata.create_all().
    location: WKBElement | WKTElement | None = Field(
        default=None,
        sa_type=Geography(  # type: ignore[call-overload]
            geometry_type="POINT", srid=4326, spatial_index=False
        ),
    )
    geocode_source: GeocodeSource | None = Field(
        default=None,
        sa_type=SqlEnum(  # type: ignore[call-overload]
            GeocodeSource, native_enum=False, create_constraint=False
        ),
    )
    needs_review: bool = Field(default=False, index=True)
    website: str | None = None
    # none_as_null=True — see the identical note on Film.genres above.
    accepted_passes: list[str] | None = Field(
        default=None,
        sa_type=JSONB(none_as_null=True),  # type: ignore[call-overload]
    )
    paris_cine_info_tid: str | None = Field(default=None, unique=True)
    offi_slug: str | None = Field(default=None, unique=True)
    cnc_id: str | None = Field(default=None, unique=True, index=True)
    insee_code: str | None = None
    screen_count: int | None = None
    seat_count: int | None = None
    art_et_essai: bool | None = None
    # sa_type accepts a configured type instance at runtime — see FilmRating.source.
    art_et_essai_grade: ArtEtEssaiGrade | None = Field(
        default=None,
        sa_type=SqlEnum(  # type: ignore[call-overload]
            ArtEtEssaiGrade, native_enum=False, create_constraint=False
        ),
    )
    # none_as_null=True — see the identical note on Film.genres above.
    art_et_essai_labels: list[str] | None = Field(
        default=None,
        sa_type=JSONB(none_as_null=True),  # type: ignore[call-overload]
    )
    is_multiplex: bool | None = None
    is_active: bool | None = None

    screenings: list["ScreeningEvent"] = Relationship(back_populates="venue")
    screens: list["Screen"] = Relationship(back_populates="venue")

    @property
    def latitude(self) -> float | None:
        """Decimal-degrees latitude, derived from :attr:`location`."""
        return to_shape(self.location).y if self.location is not None else None

    @property
    def longitude(self) -> float | None:
        """Decimal-degrees longitude, derived from :attr:`location`."""
        return to_shape(self.location).x if self.location is not None else None


class Screen(SQLModel, table=True):
    """One screening room within a :class:`Venue`.

    A multiplex has several of these under one ``Venue``; a single-screen
    independent cinema has exactly one. See
    ``docs/decisions/0021-venue-screen-split-and-geocoding.md`` — this table
    exists because Paris Ciné Info's own per-room detail endpoint was
    already being fetched per screen and then discarded down to a single,
    ambiguous set of fields on ``Venue`` whenever a cinema had more than
    one room.

    Attributes:
        id: Surrogate primary key.
        venue_id: The cinema this room belongs to.
        name: Room name/label as announced (e.g. "Salle Henri Langlois"),
            when the source names individual rooms. ``None`` for a venue
            whose rooms are only distinguished by Paris Ciné Info's numeric
            ``screen_id``, with no human-readable name available.
        seat_count: Number of seats in this room, once enriched.
        screen_width_m: Screen width in metres, once enriched.
        screen_height_m: Screen height in metres, once enriched.
        paris_cine_info_screen_id: Paris Ciné Info's own per-room identifier
            within its parent theatre, once observed — the natural key
            distinguishing rooms of the same cinema across scrape runs.
    """

    __table_args__ = (UniqueConstraint("venue_id", "paris_cine_info_screen_id"),)

    id: int | None = Field(default=None, primary_key=True)
    venue_id: int = Field(foreign_key="venue.id", index=True)
    name: str | None = None
    seat_count: int | None = None
    screen_width_m: float | None = None
    screen_height_m: float | None = None
    paris_cine_info_screen_id: int | None = None

    venue: Venue = Relationship(back_populates="screens")


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
        event_type: Category of the special screening, or ``None`` when the
            screening is ordinary or not yet classified.
        is_special: Whether this screening is worth surfacing (digest-eligible).
            Set ``True`` at ingestion for any screening a scraper already
            committed to a specific :class:`EventType` for (every source
            existing before the all-screenings expansion works this way — see
            ``docs/decisions/0008-drop-allocine-width-source.md``), and
            upgraded from ``False`` by the specialness classification
            pipeline (``core/specialness/``) for ordinary-looking screenings
            that turn out to be noteworthy (rarity, venue kind, film age...).
        specialness_reasons: Which signal(s) produced the current
            ``is_special`` verdict, for auditability — e.g.
            ``["curated_source"]`` when a source's own curation already
            implies specialness, or rule/LLM-derived reasons once classified.
        specialness_confidence: Confidence of the specialness verdict, when
            derived by a rule or the LLM classifier rather than being certain
            by construction.
        starts_at: Screening start time, timezone-aware UTC.
        has_team_present: Whether the film team attends.
        description: Free-text details from the source, when provided.
        cycle_name: Retrospective/festival cycle, when announced.
        booking_url: Best available link for this screening — a direct
            ticket-purchase link when the source provides one, otherwise the
            announcement page (see ``EventRepository.ingest``).
    """

    id: int | None = Field(default=None, primary_key=True)
    dedup_key: str = Field(unique=True, index=True)

    film_id: int = Field(foreign_key="film.id", index=True)
    venue_id: int = Field(foreign_key="venue.id", index=True)

    # sa_type accepts a configured type instance at runtime — see FilmRating.source.
    event_type: EventType | None = Field(
        default=None,
        sa_type=SqlEnum(  # type: ignore[call-overload]
            EventType, native_enum=False, create_constraint=False
        ),
    )
    is_special: bool = Field(default=False, index=True)
    # none_as_null=True — see the identical note on Film.genres above.
    specialness_reasons: list[str] | None = Field(
        default=None,
        sa_type=JSONB(none_as_null=True),  # type: ignore[call-overload]
    )
    specialness_confidence: float | None = None
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
    # sa_type accepts a configured type instance at runtime — see FilmRating.source.
    source: Source = Field(
        sa_type=SqlEnum(  # type: ignore[call-overload]
            Source, native_enum=False, create_constraint=False
        )
    )
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
