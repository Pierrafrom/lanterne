"""Deterministic deduplication key for screening events.

A single special screening is frequently announced by more than one source
(e.g. an avant-première listed both on premiereprojo.fr and sortiraparis.com).
The dedup key collapses those duplicates to one logical event: it is derived
only from the intrinsic identity of a screening — its film, its venue, and its
exact start time — never from the source it came from.

This module owns the *key computation* (a pure function). The *merge strategy*
on key collision (insert-or-update) lives in the persistence layer.
"""

import hashlib
from datetime import datetime

_APOSTROPHE_VARIANTS = str.maketrans(
    {
        "’": "'",  # RIGHT SINGLE QUOTATION MARK — the typographic apostrophe
        "‘": "'",  # LEFT SINGLE QUOTATION MARK
        "`": "'",  # GRAVE ACCENT
        "´": "'",  # ACUTE ACCENT
    }
)


def normalize_text(text: str) -> str:
    """Lower-case, unify apostrophes, and collapse whitespace for comparison.

    Shared by the dedup key and by the natural keys of :class:`Film`
    (``title_key``) and :class:`Venue` (``slug``), so "Le Grand Rex" and
    "le  grand rex" resolve to the same row. French titles are announced with
    either a straight ``'`` or a typographic ``’`` apostrophe depending on the
    source (e.g. "L'Écologie" vs "L’Écologie") — treating them as distinct
    text silently created two `Film` rows for the same title, one of which
    then collided with the other's TMDB match on the ``tmdb_id`` unique
    constraint and crashed ingestion.

    Args:
        text: Scraped text to normalize.

    Returns:
        The lower-cased text, apostrophe variants unified to ``'``, with runs
        of whitespace collapsed to one space.
    """
    return " ".join(text.translate(_APOSTROPHE_VARIANTS).lower().split())


# Known venue-name variants that normalize_text alone does not unify: sources
# occasionally announce the same physical venue under different wording
# (a shorter form, a digit spelled out, a room name in a different order).
# Left unmapped, each variant gets its own Venue row and splits that venue's
# screenings across rows instead of collapsing them — keyed by the
# normalized alias, mapped to the display name used everywhere else.
_VENUE_ALIASES: dict[str, str] = {
    "forum des images": "Le Forum des images",
    "cinémathèque française salle georges franju": (
        "La Cinémathèque française - Salle Georges Franju"
    ),
    "la cinémathèque française salle georges franju": (
        "La Cinémathèque française - Salle Georges Franju"
    ),
    "salle georges franju, paris": "La Cinémathèque française - Salle Georges Franju",
    "salle henri langlois, la cinémathèque française": (
        "La Cinémathèque française - Salle Henri Langlois"
    ),
    "paris cinémathèque": "La Cinémathèque française",
    "mk2 bastille (fg st antoine)": "MK2 Bastille (côté Fg St Antoine)",
    "les cinq caumartin": "Les 5 Caumartin",
    "sept parnassiens": "Les 7 Parnassiens",
}


def canonicalize_venue_name(name: str) -> str:
    """Map a known venue-name alias to the display name used everywhere else.

    Args:
        name: Venue name as announced by a source.

    Returns:
        The canonical display name, or ``name`` unchanged if it has no known
        alias.
    """
    return _VENUE_ALIASES.get(normalize_text(name), name)


def compute_dedup_key(title: str, venue: str, starts_at: datetime) -> str:
    """Compute the deduplication key identifying a unique screening.

    The key is stable across runs and across sources: the same film at the same
    venue and start time always yields the same key, regardless of casing,
    surrounding whitespace, or a known venue-name alias in the scraped text.

    Args:
        title: Film title as scraped (casing/whitespace insensitive).
        venue: Venue or cinema name (casing/whitespace/alias insensitive).
        starts_at: Exact screening start time, timezone-aware UTC.

    Returns:
        A hexadecimal SHA-256 digest used as the unique key in storage.
    """
    canonical_venue = normalize_text(canonicalize_venue_name(venue))
    raw = f"{normalize_text(title)}|{canonical_venue}|{starts_at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
