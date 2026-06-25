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


def _normalize(text: str) -> str:
    """Lower-case and collapse surrounding whitespace for stable comparison."""
    return " ".join(text.lower().split())


def compute_dedup_key(title: str, venue: str, starts_at: datetime) -> str:
    """Compute the deduplication key identifying a unique screening.

    The key is stable across runs and across sources: the same film at the same
    venue and start time always yields the same key, regardless of casing or
    surrounding whitespace in the scraped text.

    Args:
        title: Film title as scraped (casing/whitespace insensitive).
        venue: Venue or cinema name (casing/whitespace insensitive).
        starts_at: Exact screening start time, timezone-aware UTC.

    Returns:
        A hexadecimal SHA-256 digest used as the unique key in storage.
    """
    raw = f"{_normalize(title)}|{_normalize(venue)}|{starts_at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
