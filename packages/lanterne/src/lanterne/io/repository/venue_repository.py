"""Read/write access to venues: resolution, merging, and enrichment.

Split out of the former monolithic ``EventRepository`` (see
``docs/architecture.md``) — every method here concerns a :class:`Venue` row
in isolation, with no knowledge of screenings or films.
"""

from geoalchemy2 import WKTElement
from sqlalchemy import delete, or_, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from lanterne.core.dedup import canonicalize_venue_name, normalize_text
from lanterne.core.models import Screen, ScreeningEvent, Source, Venue
from lanterne.core.venues import classify_venue_kind
from lanterne.io.geocoding import GeocodeResult
from lanterne.io.reference.models import OfficialEstablishment
from lanterne.io.repository._commit import commit
from lanterne.io.scrapers.base import ScreenDetail, VenueDetail

# Venue fields an official-establishment match can fill — never overwritten
# when already set (see VenueRepository.apply_official_match). Excludes
# `cnc_id` itself, which is always stamped, and `address`, handled
# separately since Venue already has a backfill-only convention for it
# (see VenueRepository.save_geocoding).
_OFFICIAL_FILL_FIELDS = (
    "insee_code",
    "screen_count",
    "seat_count",
    "art_et_essai",
    "art_et_essai_grade",
    "art_et_essai_labels",
    "is_multiplex",
    "is_active",
)

# Per-source external-id columns on Venue, in the order resolve() checks
# them — see Venue.paris_cine_info_tid/offi_slug's docstrings.
_EXTERNAL_ID_FIELD: dict[Source, str] = {
    Source.PARIS_CINE_INFO: "paris_cine_info_tid",
    Source.OFFI: "offi_slug",
}


def _external_id(venue: Venue, source: Source) -> str | None:
    """Return the external id ``venue`` already claims for ``source``, if any."""
    field = _EXTERNAL_ID_FIELD.get(source)
    return getattr(venue, field) if field is not None else None


def _stamp_external_id(venue: Venue, source: Source, external_id: str) -> None:
    """Set ``venue``'s external id for ``source``, in place, unless already claimed."""
    field = _EXTERNAL_ID_FIELD.get(source)
    if field is not None and getattr(venue, field) is None:
        setattr(venue, field, external_id)


def _apply_venue_detail(venue: Venue, detail: VenueDetail) -> bool:
    """Copy the cinema-level fields of ``detail`` onto ``venue``, in place.

    A ``None`` field on ``detail`` never overwrites an already-known value —
    the endpoint omitting a field is not evidence the value changed to
    unknown. Room-level fields (``detail.screens``) are handled separately by
    :meth:`VenueRepository._upsert_screens`, not here.

    Args:
        venue: The venue row to update, mutated in place.
        detail: The freshly-fetched detail to apply.

    Returns:
        Whether any field actually changed.
    """
    changed = False
    for field in ("address", "website"):
        value = getattr(detail, field)
        if value is not None and getattr(venue, field) != value:
            setattr(venue, field, value)
            changed = True
    return changed


def _apply_screen_detail(screen: Screen, detail: ScreenDetail) -> bool:
    """Copy every known field of ``detail`` onto ``screen``, in place.

    Same "never overwrite with unknown" rule as :func:`_apply_venue_detail`.

    Args:
        screen: The screen row to update, mutated in place.
        detail: The freshly-fetched detail to apply.

    Returns:
        Whether any field actually changed.
    """
    changed = False
    for field in ("name", "seat_count", "screen_width_m", "screen_height_m"):
        value = getattr(detail, field)
        if value is not None and getattr(screen, field) != value:
            setattr(screen, field, value)
            changed = True
    return changed


class VenueRepository:
    """Read/write access to persisted venues."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to an active session.

        Args:
            session: Active async session bound to the target database.
        """
        self._session = session

    async def resolve(
        self, name: str, external_id: str | None, source: Source
    ) -> Venue:
        """Return the venue row for an announced name, creating it if new.

        When the source provides a stable ``external_id`` (see
        ``Venue.paris_cine_info_tid``/``Venue.offi_slug``), it is checked
        first and takes priority over the name-based ``slug`` match: it is
        the more reliable signal that two differently-worded names refer to
        the same physical venue. A row already matched by ``external_id`` is
        always the one returned; a *different* row also matched by ``slug``
        is retired into it via :meth:`merge_venue` — this is what lets a
        pre-existing fragmented venue (created before its external id was
        known, or from a source with no such id) self-heal into one row the
        next time a showtime carrying the id is ingested (see
        ``docs/decisions/0012-retire-lechampo.md``'s venue-name
        fragmentation follow-up). A slug match with no external id yet for
        this source is stamped with one rather than merged, since no other
        row claims it.

        The opposite failure mode — two physically distinct venues that
        happen to *announce the same display name* — is checked too:
        confirmed live (2026-07-22), offi.fr has homonym cinemas both
        reporting as plain "La Grange" (Roissy-en-Brie and Vaux-le-Pénil).
        If ``slug_match`` already claims a *different* external id for this
        source, it is not the same venue as the incoming one — a new row is
        created instead of silently conflating the two venues' screenings,
        named with a ``(#<external_id>)`` suffix to keep ``slug`` unique
        until a human gives it a proper disambiguated name.

        Args:
            name: Venue name as announced by the source.
            external_id: The source's stable venue identifier, or ``None``
                for a source that does not provide one.
            source: Which source is resolving this venue — selects which of
                ``Venue``'s per-source external-id columns to check/stamp.

        Returns:
            The resolved (possibly newly created, possibly just-merged-into)
            venue row.
        """
        tid_match = (
            await self.find_venue_by_external_id(external_id)
            if external_id is not None
            else None
        )
        slug_match = await self._find_venue(name)
        if tid_match is not None:
            if slug_match is not None and slug_match.id != tid_match.id:
                await self.merge_venue(loser=slug_match, winner=tid_match)
            return tid_match

        homonym = (
            external_id is not None
            and slug_match is not None
            and _external_id(slug_match, source) not in (None, external_id)
        )
        if slug_match is not None and not homonym:
            if external_id is not None:
                _stamp_external_id(slug_match, source, external_id)
            return slug_match

        canonical = canonicalize_venue_name(name)
        slug = normalize_text(canonical)
        if homonym and external_id is not None:
            canonical = f"{canonical} (#{external_id})"
            slug = f"{slug}#{external_id}"
        venue = Venue(slug=slug, name=canonical, kind=classify_venue_kind(canonical))
        if external_id is not None:
            _stamp_external_id(venue, source, external_id)
        return venue

    async def find_venue_by_external_id(self, external_id: str) -> Venue | None:
        """Return the venue row already matched to a source's external id, if any.

        Used by :meth:`resolve` to recognize the same physical venue across
        sources that describe it with different wording — see
        ``Venue.paris_cine_info_tid``/``Venue.offi_slug``. Checks every
        source's external-id column: the two id schemes (Paris Ciné Info's
        short alphanumeric theatre codes, offi.fr's hyphenated URL slugs)
        are lexically distinct enough that a cross-source collision isn't a
        practical concern, and this keeps the lookup source-agnostic —
        :meth:`resolve` is what decides *which* column to stamp on a write.

        Args:
            external_id: The source-provided stable venue identifier to
                look up (e.g. a Paris Ciné Info theatre id or an offi.fr
                venue-page slug).

        Returns:
            The venue row already holding this external id, or ``None``.
        """
        statement = select(Venue).where(
            or_(
                col(Venue.paris_cine_info_tid) == external_id,
                col(Venue.offi_slug) == external_id,
            )
        )
        result = await self._session.exec(statement)
        return result.first()

    async def merge_venue(self, loser: Venue, winner: Venue) -> None:
        """Repoint every screening from a duplicate venue row onto the canonical one.

        Called when a source's stable external id reveals that two
        differently-worded venue names are the same physical venue.
        ``Venue`` has a single dependent foreign key
        (``ScreeningEvent.venue_id``), so the merge is a straightforward bulk
        repoint-then-delete, no per-row ORM loads needed — same shape as
        ``FilmRepository.merge_film``.

        Args:
            loser: The duplicate venue row to retire; deleted by this call.
            winner: The venue row every screening should point to instead.
        """
        loser_id, winner_id = loser.id, winner.id
        await self._session.exec(
            update(ScreeningEvent)
            .where(col(ScreeningEvent.venue_id) == loser_id)
            .values(venue_id=winner_id)
        )
        await self._session.exec(delete(Venue).where(col(Venue.id) == loser_id))
        await commit(self._session)

    async def update_venue_passes(self, passes: dict[str, list[str]]) -> None:
        """Enrich already-stored venues with their accepted subscription cards.

        Only enriches venues that already exist from an actual screening —
        never creates a ``Venue`` row from this data alone, since a venue
        with no stored screening is out of scope regardless of which passes
        it accepts (see ``pipeline.py::IngestionPipeline._apply_venue_passes``
        and ``ParisCineInfoScraper.fetch_venue_passes``).

        Args:
            passes: A mapping of venue display name (as announced by the
                source) to its accepted card codes, matched to a stored
                venue via the same slug/alias resolution as :meth:`resolve`.
        """
        changed = False
        for name, cards in passes.items():
            venue = await self._find_venue(name)
            if venue is None:
                continue
            sorted_cards = sorted(set(cards))
            if venue.accepted_passes != sorted_cards:
                venue.accepted_passes = sorted_cards
                self._session.add(venue)
                changed = True
        if changed:
            await commit(self._session)

    async def update_venue_details(self, details: dict[str, VenueDetail]) -> None:
        """Enrich already-stored venues with address/website/per-room detail.

        Same scope rule as :meth:`update_venue_passes`: only enriches venues
        that already exist from an actual screening, matched via the same
        slug/alias resolution. Each of ``detail.screens`` is upserted into
        its own :class:`Screen` row via :meth:`_upsert_screens`.

        Args:
            details: A mapping of venue display name (as announced by the
                source) to its :class:`~lanterne.io.scrapers.base.VenueDetail`
                (see ``ParisCineInfoScraper.fetch_venue_details``).
        """
        changed = False
        for name, detail in details.items():
            venue = await self._find_venue(name)
            if venue is None:
                continue
            if _apply_venue_detail(venue, detail):
                self._session.add(venue)
                changed = True
            if await self._upsert_screens(venue, detail.screens):
                changed = True
        if changed:
            await commit(self._session)

    async def _upsert_screens(
        self, venue: Venue, screens: tuple[ScreenDetail, ...]
    ) -> bool:
        """Create or update one :class:`Screen` row per screen detail with a known id.

        Matched by ``(venue_id, paris_cine_info_screen_id)`` so the same room
        is recognized (and updated in place) across scrape runs rather than
        duplicated. A screen detail with no ``external_id`` can't be reliably
        matched across runs and is skipped rather than risking a duplicate
        row every time this runs.

        Args:
            venue: The venue whose screens are being enriched; must already
                be persisted (``venue.id`` set).
            screens: Every screen detail fetched for this venue this run.

        Returns:
            Whether any Screen row was created or changed.
        """
        changed = False
        for screen_detail in screens:
            if screen_detail.external_id is None:
                continue
            statement = select(Screen).where(
                col(Screen.venue_id) == venue.id,
                col(Screen.paris_cine_info_screen_id) == screen_detail.external_id,
            )
            result = await self._session.exec(statement)
            screen = result.first()
            if screen is None:
                screen = Screen(
                    venue_id=venue.id,
                    paris_cine_info_screen_id=screen_detail.external_id,
                )
                changed = True
            if _apply_screen_detail(screen, screen_detail):
                changed = True
            self._session.add(screen)
        return changed

    async def list_venues_missing_coordinates(self) -> list[Venue]:
        """Return every venue with no resolved GPS coordinates yet.

        Used by the ``geocode-venues`` CLI command to find its work — see
        ``io/geocoding.py``.

        Returns:
            Every :class:`Venue` row still missing coordinates.
        """
        statement = select(Venue).where(col(Venue.location).is_(None))
        result = await self._session.exec(statement)
        return list(result.all())

    async def save_geocoding(self, venue: Venue, result: GeocodeResult) -> None:
        """Persist a venue's freshly resolved coordinates.

        Also backfills ``venue.address`` from the result when the venue had
        none — see :func:`~lanterne.io.geocoding.geocode_name`'s formatted
        address, part of the "every venue needs a valid address" goal (see
        ``docs/decisions/0021-venue-screen-split-and-geocoding.md``). Only
        for a *confirmed* match (``needs_review=False``): backfilling from
        an unconfirmed name-search guess is how a wrong address survives a
        later reset — confirmed live (see
        ``docs/decisions/0024-venue-data-quality-cleanup.md``): a wrong
        guessed address, once written here, gets falsely re-trusted as
        precise on any future geocoding pass, since a non-empty
        ``venue.address`` makes tier 1 (address lookup, always
        ``needs_review=False``) run instead of tier 2 next time.

        Args:
            venue: The venue row to update, mutated in place.
            result: The resolved coordinates and their confidence.
        """
        venue.location = WKTElement(
            f"POINT({result.longitude} {result.latitude})", srid=4326
        )
        venue.geocode_source = result.source
        venue.needs_review = result.needs_review
        should_backfill_address = (
            venue.address is None
            and result.address is not None
            and not result.needs_review
        )
        if should_backfill_address:
            venue.address = result.address
        self._session.add(venue)
        await commit(self._session)

    async def _find_venue(self, name: str) -> Venue | None:
        """Return the venue row matching an announced name, or None."""
        slug = normalize_text(canonicalize_venue_name(name))
        statement = select(Venue).where(Venue.slug == slug)
        result = await self._session.exec(statement)
        return result.first()

    async def list_all_venues(self) -> list[Venue]:
        """Return every stored venue.

        Used by the ``sync-reference`` CLI command to match every venue
        against the official CNC referential (see
        ``io/reference/matching.py``) — unlike
        :meth:`list_venues_missing_coordinates`, this is not scoped to a
        gap, since a venue may need official-referential enrichment
        regardless of whether it is already geocoded.

        Returns:
            Every :class:`Venue` row.
        """
        result = await self._session.exec(select(Venue))
        return list(result.all())

    async def find_venue_by_cnc_id(self, cnc_id: str) -> Venue | None:
        """Return the venue row already matched to a CNC establishment id, if any.

        Args:
            cnc_id: The CNC's own establishment authorization number.

        Returns:
            The venue row already holding this ``cnc_id``, or ``None``.
        """
        statement = select(Venue).where(col(Venue.cnc_id) == cnc_id)
        result = await self._session.exec(statement)
        return result.first()

    async def apply_official_match(
        self, venue: Venue, establishment: OfficialEstablishment
    ) -> None:
        """Stamp a venue with its matched official establishment's identity.

        Only ever fills a field that is currently ``None`` — an official
        match must never downgrade or overwrite a value a previous scrape
        or manual correction already set (the same safe subset
        :func:`_apply_venue_detail` already uses for scraped venue detail).
        Idempotent: re-applying the same match changes nothing further.

        Args:
            venue: The venue row to update, mutated in place.
            establishment: The matched official establishment (see
                ``io/reference/matching.py::match_establishments``).
        """
        if venue.cnc_id is None:
            venue.cnc_id = establishment.cnc_id
        if venue.address is None and establishment.address is not None:
            venue.address = establishment.address
        for field in _OFFICIAL_FILL_FIELDS:
            if getattr(venue, field) is None:
                value = getattr(establishment, field)
                if value is not None:
                    setattr(venue, field, value)
        self._session.add(venue)
        await commit(self._session)

    async def save_venue(self, venue: Venue) -> None:
        """Persist in-place changes to a venue row.

        Args:
            venue: The venue row to save.
        """
        self._session.add(venue)
        await commit(self._session)
