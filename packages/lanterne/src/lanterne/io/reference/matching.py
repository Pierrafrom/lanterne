"""Matching official establishments against existing scraped venues.

The single hard rule this module enforces: automatic matching is only
allowed when name, department, and distance all agree — no fuzzy name
similarity at any threshold, no "2 out of 3". Anything less than the full
triple is a candidate for manual review, never applied automatically. See
``docs/decisions/0026-official-reference-data-layer.md``, and the "La
Grange" homonym incident (``docs/decisions/0024-venue-data-quality-cleanup.md``)
this rule exists to never repeat.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from lanterne.core.dedup import canonicalize_venue_name, normalize_text
from lanterne.core.geo import haversine_km
from lanterne.core.models import Venue
from lanterne.io.reference.models import OfficialEstablishment

# A cinema auto-matched by name+department is still rejected if its
# geocoded location is more than this far from the official coordinates —
# tight enough to reject a different nearby building, generous enough for
# the imprecision already documented for tier-2 (name-search) geocoding
# (see docs/decisions/0021-venue-screen-split-and-geocoding.md).
_AUTO_MATCH_DISTANCE_METERS = 300

# Matches a 5-digit French postal code anywhere in a free-text address —
# its first two digits equal the département for every Île-de-France
# address, giving a department-level proxy for INSEE matching without
# requiring Venue to already carry a real insee_code (which it only gets
# from this very matching process).
_POSTAL_CODE_PATTERN = re.compile(r"\b(\d{5})\b")

# A leading French definite article, stripped before comparing names — the
# CNC/IDF datasets spell establishments without one ("GRAND REX") while
# scraped venue names commonly carry one ("Le Grand Rex"). This is a
# deterministic normalization step, not fuzzy similarity: it never changes
# whether two *different* names are treated as equal, only whether the
# same name spelled with or without its article is.
_LEADING_ARTICLE_PATTERN = re.compile(r"^(le|la|les)\s+|^l'\s*")


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    """One (establishment, venue) pair considered for matching.

    Attributes:
        establishment: The official establishment being matched.
        venue: The existing scraped venue considered as its match.
        name_match: Whether the normalized names are exactly equal.
        department_match: Whether the venue's address-derived département
            agrees with the establishment's.
        distance_match: Whether the venue's geocoded location is within
            :data:`_AUTO_MATCH_DISTANCE_METERS` of the establishment's
            coordinates.
        distance_km: The computed distance, when both sides had
            coordinates to compare — for the manual-review report, not a
            gate on its own.
        is_duplicate: Whether another, lower-id venue already claims this
            establishment via the full automatic triple — set by
            :func:`match_establishments`, never by the per-pair checks.
    """

    establishment: OfficialEstablishment
    venue: Venue
    name_match: bool
    department_match: bool
    distance_match: bool
    distance_km: float | None
    is_duplicate: bool = False

    @property
    def is_automatic(self) -> bool:
        """Whether every check passes and no other venue already claims this match."""
        return (
            self.name_match
            and self.department_match
            and self.distance_match
            and not self.is_duplicate
        )


@dataclass(frozen=True, slots=True)
class ReferenceSyncOutcome:
    """Outcome counts of one ``sync-reference`` run.

    Lives alongside :class:`MatchCandidate` rather than in ``main.py`` so
    both ``main.py`` (which produces it) and ``io/console.py`` (which
    prints it) can import it without either depending on the other — same
    reasoning as ``EventStats``/``IngestionReport`` living in ``core/``.

    Attributes:
        loaded: Total Île-de-France establishments loaded from the merged
            CNC/IDF referential.
        auto_matched: Establishments matched to a venue on the full
            name+department+distance triple — applied to the database
            unless ``--dry-run`` was passed.
        candidates: Every candidate that did *not* pass the full triple,
            for the printed manual-review report. Never applied.
    """

    loaded: int
    auto_matched: int
    candidates: tuple[MatchCandidate, ...]


def match_establishments(
    establishments: Sequence[OfficialEstablishment], venues: Sequence[Venue]
) -> list[MatchCandidate]:
    """Compute match candidates between official establishments and venues.

    Only pairs whose names agree are returned at all — matching every
    establishment against every venue regardless of name would flood the
    result with pairs that share no plausible identity; a "candidate"
    implies at least a name match. An establishment or venue already
    linked via a stamped ``Venue.cnc_id`` is skipped entirely, so re-running
    this after a previous sync only considers what is still unresolved.

    Args:
        establishments: Official establishments to match, already scoped
            to Île-de-France (see
            ``io/reference/models.py::merge_establishments``).
        venues: Every existing venue to match against.

    Returns:
        One :class:`MatchCandidate` per name-matching pair, with
        ``is_duplicate`` set on every candidate but the lowest-id venue
        when more than one venue would otherwise auto-match the same
        establishment.
    """
    linked_cnc_ids = {venue.cnc_id for venue in venues if venue.cnc_id is not None}
    unlinked_venues = [venue for venue in venues if venue.cnc_id is None]
    candidates = [
        _evaluate(venue, establishment)
        for establishment in establishments
        if establishment.cnc_id not in linked_cnc_ids
        for venue in unlinked_venues
        if _check_name(venue, establishment)
    ]
    return _resolve_duplicates(candidates)


def _evaluate(venue: Venue, establishment: OfficialEstablishment) -> MatchCandidate:
    """Build the candidate for one (venue, establishment) pair."""
    distance_match, distance_km = _check_distance(venue, establishment)
    return MatchCandidate(
        establishment=establishment,
        venue=venue,
        name_match=True,  # only called for pairs that already passed _check_name
        department_match=_check_department(venue, establishment),
        distance_match=distance_match,
        distance_km=distance_km,
    )


def _check_name(venue: Venue, establishment: OfficialEstablishment) -> bool:
    """Whether the venue's and establishment's names are exactly equal, normalized."""
    venue_name = _strip_leading_article(
        normalize_text(canonicalize_venue_name(venue.name))
    )
    establishment_name = _strip_leading_article(normalize_text(establishment.name))
    return venue_name == establishment_name


def _strip_leading_article(normalized_name: str) -> str:
    """Remove one leading French definite article from an already-normalized name."""
    return _LEADING_ARTICLE_PATTERN.sub("", normalized_name, count=1)


def _check_department(venue: Venue, establishment: OfficialEstablishment) -> bool:
    """Whether the venue's address-derived département matches the establishment's."""
    department = _department_from_address(venue.address)
    return department is not None and department == establishment.dep


def _department_from_address(address: str | None) -> str | None:
    """Extract a French département code from a free-text address, if present."""
    if address is None:
        return None
    match = _POSTAL_CODE_PATTERN.search(address)
    return match.group(1)[:2] if match else None


def _check_distance(
    venue: Venue, establishment: OfficialEstablishment
) -> tuple[bool, float | None]:
    """Whether the venue's geocoded location is close enough to the establishment's."""
    venue_latitude, venue_longitude = venue.latitude, venue.longitude
    if (
        establishment.latitude is None
        or establishment.longitude is None
        or venue_latitude is None
        or venue_longitude is None
    ):
        return False, None
    distance_km = haversine_km(
        venue_latitude, venue_longitude, establishment.latitude, establishment.longitude
    )
    return distance_km * 1000 <= _AUTO_MATCH_DISTANCE_METERS, distance_km


def _resolve_duplicates(candidates: list[MatchCandidate]) -> list[MatchCandidate]:
    """Flag every candidate but the lowest-id venue when several auto-match one.

    "One" meaning one establishment — several venues auto-matching it is a
    pre-existing unmerged venue fragment, not a new problem this creates.
    """
    automatic_by_establishment: dict[str, list[MatchCandidate]] = defaultdict(list)
    for candidate in candidates:
        if (
            candidate.name_match
            and candidate.department_match
            and candidate.distance_match
        ):
            automatic_by_establishment[candidate.establishment.cnc_id].append(candidate)

    duplicate_keys: set[tuple[str, int | None]] = set()
    for group in automatic_by_establishment.values():
        if len(group) > 1:
            ordered = sorted(group, key=lambda candidate: candidate.venue.id or 0)
            duplicate_keys.update(
                (loser.establishment.cnc_id, loser.venue.id) for loser in ordered[1:]
            )

    return [
        replace(candidate, is_duplicate=True)
        if (candidate.establishment.cnc_id, candidate.venue.id) in duplicate_keys
        else candidate
        for candidate in candidates
    ]
