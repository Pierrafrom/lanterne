"""Venue-kind classification — a prior signal for the specialness pipeline.

A chain name (UGC, Pathé, MK2...) or one of the four patrimonial
institutions is reliably identifiable from the venue name alone, seeded
from the network confirmed live in ``docs/coverage-matrix.md`` (78 venues
across Paris Ciné Info, plus the four institution-only sources). No
external config file is needed: this mirrors the small hardcoded alias
dict already used for venue-name canonicalization (see
``core/dedup.py``'s ``_VENUE_ALIASES``), rather than introducing a new
config layer for a handful of known prefixes.

Also holds :func:`pass_label`, the French display label for a subscription
card code (``Venue.accepted_passes``) — confirmed live from Paris Ciné
Info's authenticated homepage (the ``cineOptions`` catalogue parsed by
``io/scrapers/paris_cine_info.py::ParisCineInfoScraper.fetch_venue_passes``).
"""

from cine_event_bot.core.dedup import normalize_text
from cine_event_bot.core.models import VenueKind

# Exact, normalized names of the four patrimonial institutions covered by
# their own bespoke scrapers (absent from every aggregator — see ADR 0008).
_INSTITUTION_NAMES = {
    normalize_text(name)
    for name in (
        "La Cinémathèque française",
        "La Cinémathèque française - Salle Georges Franju",
        "La Cinémathèque française - Salle Henri Langlois",
        "Le Forum des images",
        "Fondation Jérôme Seydoux-Pathé",
        "Cinéma en plein air de La Villette",
    )
}

# Chain name prefixes, checked against the normalized venue name.
_CHAIN_PREFIXES: tuple[tuple[str, VenueKind], ...] = (
    ("ugc", VenueKind.CHAIN_UGC),
    ("pathé", VenueKind.CHAIN_PATHE),  # normalize_text lower-cases but keeps accents
    ("mk2", VenueKind.CHAIN_MK2),
    ("cgr", VenueKind.CHAIN_OTHER),
)

# French display label per subscription-card code, confirmed live from Paris
# Ciné Info's ``cineOptions`` catalogue (its own filter-dropdown labels).
_PASS_LABELS: dict[str, str] = {
    "ugc": "UGC Illimité",
    "pass": "Pathé CinéPass",
    "librepass": "Cinémathèque LibrePass",
    "cip": "Ciné Carte CIP",
    "cinecarte": "Pathé CinéCarte",
    "multicine": "Le Club Multiciné",
    "mk2": "MK2 Carte 5",
    "dulac": "Maison Dulac Cinema",
    "lacarte": "UGC 'la carte'",
    "cinecheques": "Cinéchèques",
}


def pass_label(card_code: str) -> str:
    """Return a subscription card's French display label.

    Args:
        card_code: A card code as stored in ``Venue.accepted_passes`` (e.g.
            ``"ugc"``, ``"pass"``).

    Returns:
        The known French label, or the raw code unchanged when it is not
        one of the confirmed :data:`_PASS_LABELS` entries.
    """
    return _PASS_LABELS.get(card_code, card_code)


def classify_venue_kind(name: str) -> VenueKind:
    """Classify a venue name into its broad kind.

    Args:
        name: Venue name as announced (any casing/spacing).

    Returns:
        :data:`VenueKind.INSTITUTION` for one of the four patrimonial
        sources, the matching :data:`VenueKind.CHAIN_*` member for a known
        chain prefix, or :data:`VenueKind.INDEPENDENT` otherwise.
    """
    normalized = normalize_text(name)
    if normalized in _INSTITUTION_NAMES:
        return VenueKind.INSTITUTION
    for prefix, kind in _CHAIN_PREFIXES:
        if normalized.startswith(prefix):
            return kind
    return VenueKind.INDEPENDENT
