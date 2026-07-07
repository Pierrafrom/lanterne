"""Scraper for MK2 (mk2.com).

Confirmed Next.js: the ``/evenements`` page server-renders its event lists as
JSON inside the React Server Components stream
(``self.__next_f.push([1,"…"])``), the same mechanism as Première Projo (see
ADR 0004) — no LLM needed. Each ``"type":"event-list"`` object groups events
under a named category (e.g. "AVANT-PREMIÈRES AVEC ÉQUIPE"); each event
already carries a machine-readable ``type.id`` ("avant-premiere", "festival",
"cinema-club", "conferences") and, when the film's team attends, a
``"equipe-du-film"`` genre tag — the exact ``has_team_present`` signal other
sources need an LLM to infer from prose.

Only categories where an event's ``name`` is reliably one specific film's
title are mapped: "avant-premiere" and "festival". "cinema-club" entries
(e.g. "Cultissime") name a recurring monthly programme, not the specific film
showing next — resolving that would need a further, unexplored API call, so
these are skipped rather than mapped incorrectly. "conferences" entries are
lecture cycles, not screenings, and are skipped too.

Known limitation for chains, confirmed against live data (not hypothetical):
an event's data only ever carries one ``nextSession`` (one cinema, one time),
never a full per-cinema schedule, even when ``linkedCinemas`` lists several
MK2 venues — the per-cinema showtimes live behind a client-side call the
``/evenements/<slug>`` detail page's static HTML does not expose (same class
of gap as UGC's JS-rendered events page). Real, currently-mapped events
routinely link 4-5 cinemas at once (e.g. an "Avant-premières Little Films
Festival" entry spanning five MK2 rooms) while carrying only one
``nextSession`` — so this drops real screenings today, not just in a
hypothetical future case. Rather than fabricate an assumed-identical time for
the other venues, a multi-cinema event logs a warning (see
``_warn_if_multi_cinema``) so the gap is visible and monitorable rather than
silent; resolving it properly needs the same devtools-level XHR discovery
already deferred for UGC.
"""

import json
import re
from datetime import datetime
from typing import Any

import httpx

from cine_event_bot.core.models import EventType, ExtractedEvent, Sighting, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)

_EVENTS_URL = "https://www.mk2.com/evenements"
_EVENT_PAGE_URL = "https://www.mk2.com/evenements/{slug}"
_RSC_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.DOTALL)
_EVENT_LIST_START = re.compile(r'\{"slug":"[^"]+","type":"event-list","title":')
_TEAM_GENRE = "equipe-du-film"

_EVENT_TYPE_MAP = {
    "avant-premiere": EventType.AVANT_PREMIERE,
    "festival": EventType.FESTIVAL,
}


class Mk2Scraper:
    """Scrapes avant-premières and festivals from mk2.com's embedded RSC JSON."""

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        return Source.MK2

    def parse_events(self, html: str) -> list[Sighting]:
        """Extract screenings from the page's embedded RSC JSON.

        Args:
            html: HTML of the mk2.com ``/evenements`` page.

        Returns:
            One :class:`Sighting` per mapped event; event-lists whose type is
            not in :data:`_EVENT_TYPE_MAP` (cinema clubs, conference cycles)
            are skipped entirely.
        """
        decoded = _decode_rsc(html)
        return [
            sighting
            for event_list in _event_lists(decoded)
            for sighting in _sightings(event_list)
        ]

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[Sighting]:
        """Fetch the events page and parse its embedded screenings.

        Mapping is structured (no LLM), so the sightings are produced at once
        and reported in a single batch for the progress display.

        Args:
            client: Shared async HTTP client used for the request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`Sighting` per mapped screening on the events page.
        """
        response = await client.get(_EVENTS_URL)
        response.raise_for_status()
        sightings = self.parse_events(response.text)
        reporter.events_fetched(self.source.value, len(sightings))
        for _ in sightings:
            reporter.event_processed(self.source.value)
        return sightings


def _decode_rsc(html: str) -> str:
    """Concatenate the decoded RSC stream chunks into one string."""
    return "".join(json.loads(chunk) for chunk in _RSC_CHUNK.findall(html))


def _event_lists(decoded: str) -> list[dict[str, Any]]:
    """Return every ``"type":"event-list"`` object found in the decoded stream."""
    lists = []
    for match in _EVENT_LIST_START.finditer(decoded):
        obj_text = _scan_object(decoded, match.start())
        if obj_text is None:
            continue
        try:
            lists.append(json.loads(obj_text))
        except json.JSONDecodeError:
            continue
    return lists


def _scan_object(text: str, start: int) -> str | None:
    """Return the brace-balanced JSON object starting at ``text[start] == '{'``.

    Tracks string state so braces inside JSON string values do not affect the
    depth count.
    """
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif not in_string:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
    return None


def _sightings(event_list: dict[str, Any]) -> list[Sighting]:
    """Map an event-list's mapped-type events to sightings, skipping the rest."""
    return [
        sighting
        for event in event_list.get("events") or []
        if (sighting := _sighting(event)) is not None
    ]


def _sighting(event: dict[str, Any]) -> Sighting | None:
    """Map one MK2 event to a Sighting, or None if unmapped or malformed."""
    type_id = (event.get("type") or {}).get("id")
    event_type = _EVENT_TYPE_MAP.get(type_id) if isinstance(type_id, str) else None
    title = event.get("name")
    slug = event.get("slug")
    next_session = event.get("nextSession") or {}
    starts_at = _parse_datetime(next_session.get("showTime"))
    venue = _venue(event)
    if (
        event_type is None
        or not isinstance(title, str)
        or not isinstance(slug, str)
        or starts_at is None
        or venue is None
    ):
        return None
    _warn_if_multi_cinema(title, event.get("linkedCinemas") or [])
    genres = {genre.get("id") for genre in event.get("genres") or []}
    extracted = ExtractedEvent(
        title=title,
        event_type=event_type,
        venue=venue,
        starts_at=starts_at,
        has_team_present=_TEAM_GENRE in genres,
        description=event.get("description")
        if isinstance(event.get("description"), str)
        else None,
    )
    return Sighting(
        extracted=extracted,
        source=Source.MK2,
        source_url=_EVENT_PAGE_URL.format(slug=slug),
    )


def _warn_if_multi_cinema(title: str, cinemas: list[dict[str, Any]]) -> None:
    """Log when an event links several cinemas — only one showing is captured."""
    if len(cinemas) > 1:
        names = [cinema.get("name") for cinema in cinemas]
        logger.warning(
            "mk2 event spans multiple cinemas — only nextSession's screening "
            "is captured, other venues' showings are silently missed",
            extra={"ctx": {"title": title, "linked_cinemas": names}},
        )


def _venue(event: dict[str, Any]) -> str | None:
    """Return the cinema name matching the next session, or the first linked one."""
    cinemas = event.get("linkedCinemas") or []
    cinema_id = (event.get("nextSession") or {}).get("cinemaId")
    for cinema in cinemas:
        if cinema.get("id") == cinema_id and isinstance(cinema.get("name"), str):
            return f"mk2 {cinema['name']}"
    if cinemas and isinstance(cinemas[0].get("name"), str):
        return f"mk2 {cinemas[0]['name']}"
    return None


def _parse_datetime(value: object) -> datetime | None:
    """Parse an ISO 8601 timestamp (with a trailing 'Z') into a UTC datetime."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
