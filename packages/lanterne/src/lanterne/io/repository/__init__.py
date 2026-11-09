"""Repositories mediating access to screening events and subscribers.

Each repository wraps an injected :class:`AsyncSession` and owns the commit
for its write operations, so callers work in terms of domain intent
(``ingest`` a sighting, ``subscribe`` a chat) rather than session mechanics.

Split into one focused class per responsibility — :class:`EventRepository`
(ingestion/dedup/querying), :class:`FilmRepository`, :class:`VenueRepository`,
and :class:`SubscriberRepository` — after an earlier quality audit flagged a
single 674-line ``EventRepository`` mixing all four concerns (see
``docs/architecture.md``). ``EventRepository`` composes the film and venue
repositories internally and re-exposes their methods as delegates, so every
existing caller keeps constructing a single ``EventRepository(session)``
without any change.
"""

from lanterne.io.repository.event_repository import EventRepository
from lanterne.io.repository.film_repository import FilmRepository
from lanterne.io.repository.subscriber_repository import SubscriberRepository
from lanterne.io.repository.venue_repository import VenueRepository

__all__ = [
    "EventRepository",
    "FilmRepository",
    "SubscriberRepository",
    "VenueRepository",
]
