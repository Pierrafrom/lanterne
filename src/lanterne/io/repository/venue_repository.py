"""Read/write access to venues: resolution, merging, and enrichment.

Split out of the former monolithic ``EventRepository`` (see
``docs/architecture.md``) — every method here concerns a :class:`Venue` row
in isolation, with no knowledge of screenings or films.
"""

from sqlalchemy import delete, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from lanterne.core.dedup import canonicalize_venue_name, normalize_text
from lanterne.core.models import ScreeningEvent, Venue
from lanterne.core.venues import classify_venue_kind
from lanterne.io.repository._commit import commit
from lanterne.io.scrapers.base import VenueDetail


def _apply_venue_detail(venue: Venue, detail: VenueDetail) -> bool:
    """Copy every known field of ``detail`` onto ``venue``, in place.

    A ``None`` field on ``detail`` never overwrites an already-known value —
    the endpoint omitting a field (or the venue having multiple rooms this
    run, see :class:`~lanterne.io.scrapers.base.VenueDetailSource`)
    is not evidence the value changed to unknown.

    Args:
        venue: The venue row to update, mutated in place.
        detail: The freshly-fetched detail to apply.

    Returns:
        Whether any field actually changed.
    """
    changed = False
    fields = ("address", "website", "seat_count", "screen_width_m", "screen_height_m")
    for field in fields:
        value = getattr(detail, field)
        if value is not None and getattr(venue, field) != value:
            setattr(venue, field, value)
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

    async def resolve(self, name: str, external_id: str | None) -> Venue:
        """Return the venue row for an announced name, creating it if new.

        When the source provides a stable ``external_id`` (see
        ``Venue.paris_cine_info_tid``), it is checked first and takes
        priority over the name-based ``slug`` match: it is the more reliable
        signal that two differently-worded names refer to the same physical
        venue. A row already matched by ``external_id`` is always the one
        returned; a *different* row also matched by ``slug`` is retired into
        it via :meth:`merge_venue` — this is what lets a pre-existing
        fragmented venue (created before its external id was known, or from
        a source with no such id) self-heal into one row the next time a
        showtime carrying the id is ingested (see
        ``docs/decisions/0012-retire-lechampo.md``'s venue-name
        fragmentation follow-up). A slug match with no external id yet is
        stamped with one rather than merged, since no other row claims it.

        Args:
            name: Venue name as announced by the source.
            external_id: The source's stable venue identifier, or ``None``
                for a source that does not provide one.

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
        if slug_match is not None:
            if external_id is not None and slug_match.paris_cine_info_tid is None:
                slug_match.paris_cine_info_tid = external_id
            return slug_match
        canonical = canonicalize_venue_name(name)
        slug = normalize_text(canonical)
        return Venue(
            slug=slug,
            name=canonical,
            kind=classify_venue_kind(canonical),
            paris_cine_info_tid=external_id,
        )

    async def find_venue_by_external_id(self, external_id: str) -> Venue | None:
        """Return the venue row already matched to a source's external id, if any.

        Used by :meth:`resolve` to recognize the same physical venue across
        sources that describe it with different wording — see
        ``Venue.paris_cine_info_tid``.

        Args:
            external_id: The source-provided stable venue identifier to
                look up (e.g. a Paris Ciné Info theatre id).

        Returns:
            The venue row already holding this external id, or ``None``.
        """
        statement = select(Venue).where(Venue.paris_cine_info_tid == external_id)
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
        """Enrich already-stored venues with address/website/room detail.

        Same scope rule as :meth:`update_venue_passes`: only enriches venues
        that already exist from an actual screening, matched via the same
        slug/alias resolution.

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
        if changed:
            await commit(self._session)

    async def _find_venue(self, name: str) -> Venue | None:
        """Return the venue row matching an announced name, or None."""
        slug = normalize_text(canonicalize_venue_name(name))
        statement = select(Venue).where(Venue.slug == slug)
        result = await self._session.exec(statement)
        return result.first()
