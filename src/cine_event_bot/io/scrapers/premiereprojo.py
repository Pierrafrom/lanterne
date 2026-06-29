"""Scraper for Première Projo (premiereprojo.fr).

The site is a Next.js app that server-renders its screenings as structured JSON
embedded in the React Server Components stream (``self.__next_f.push([1,"…"])``).
That JSON is far more stable than the generated CSS, and already fully typed, so
the scraper extracts it and maps it straight to :class:`ScreeningEvent` with no
LLM call (see ``docs/scraping-strategy.md`` and ADR 0004).

Every Première Projo entry is an avant-première; the ``avpType`` field
distinguishes a plain preview (``AVP``) from one attended by the film team
(``AVPE``), which drives ``has_team_present``.
"""

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from cine_event_bot.core.models import EventType, ExtractedEvent, ScreeningEvent, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.logging_config import get_logger

logger = get_logger(__name__)

_INDEX_URL = "https://www.premiereprojo.fr/"
_TEAM_PRESENT_AVP_TYPE = "AVPE"
_RSC_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.DOTALL)
_DATA_ANCHOR = re.compile(r'"data"\s*:\s*\[')


class PremiereProjoScraper:
    """Scrapes avant-premières from premiereprojo.fr's embedded RSC JSON."""

    @property
    def source(self) -> Source:
        """The source this scraper covers."""
        return Source.PREMIERE_PROJO

    def parse_events(self, html: str) -> list[ScreeningEvent]:
        """Extract screenings from the page's embedded RSC JSON.

        Args:
            html: HTML of the premiereprojo.fr homepage.

        Returns:
            One :class:`ScreeningEvent` per (film, show) pair found. Returns an
            empty list (with a warning) if the expected payload is absent.
        """
        decoded = self._decode_rsc(html)
        movies = _extract_movies(decoded)
        if not movies:
            logger.warning(
                "premiereprojo payload not found",
                extra={"ctx": {"html_len": len(html)}},
            )
            return []
        return [event for movie in movies for event in _movie_events(movie)]

    async def fetch_events(
        self, client: httpx.AsyncClient, reporter: ProgressReporter
    ) -> list[ScreeningEvent]:
        """Fetch the homepage and parse its embedded screenings.

        Mapping is structured (no LLM), so the events are produced at once and
        reported in a single batch for the progress display.

        Args:
            client: Shared async HTTP client used for the request.
            reporter: Progress reporter for live display.

        Returns:
            One :class:`ScreeningEvent` per screening on the homepage.
        """
        response = await client.get(_INDEX_URL)
        response.raise_for_status()
        events = self.parse_events(response.text)
        reporter.events_fetched(self.source.value, len(events))
        for _ in events:
            reporter.event_processed(self.source.value)
        return events

    @staticmethod
    def _decode_rsc(html: str) -> str:
        """Concatenate the decoded RSC stream chunks into one string."""
        return "".join(json.loads(chunk) for chunk in _RSC_CHUNK.findall(html))


def _extract_movies(decoded: str) -> list[dict[str, Any]]:
    """Return the first ``"data": [...]`` array whose items look like movies.

    Scans every ``"data"`` array in the decoded stream and keeps the first one
    whose elements carry a ``shows`` key, so unrelated React Query caches in the
    same payload are ignored.
    """
    for match in _DATA_ANCHOR.finditer(decoded):
        array = _scan_array(decoded, match.end() - 1)
        if array is None:
            continue
        try:
            candidate = json.loads(array)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, list) and any(
            isinstance(item, dict) and "shows" in item for item in candidate
        ):
            return candidate
    return []


def _scan_array(text: str, start: int) -> str | None:
    """Return the bracket-balanced JSON array starting at ``text[start] == '['``.

    Tracks string state so brackets inside JSON string values do not affect the
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
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
    return None


def _movie_events(movie: dict[str, Any]) -> Iterator[ScreeningEvent]:
    """Yield one event per valid show of a film, skipping malformed shows."""
    title = movie.get("title")
    if not isinstance(title, str):
        return
    for show in movie.get("shows") or []:
        event = _show_event(title, movie.get("synopsis"), show)
        if event is not None:
            yield event


def _show_event(
    title: str, synopsis: str | None, show: dict[str, Any]
) -> ScreeningEvent | None:
    """Map one show to a ScreeningEvent, or None if essential fields are absent."""
    starts_at = _parse_datetime(show.get("date"))
    venue = (show.get("cinemas") or {}).get("name")
    if starts_at is None or not isinstance(venue, str):
        return None
    extracted = ExtractedEvent(
        title=title,
        event_type=EventType.AVANT_PREMIERE,
        venue=venue,
        starts_at=starts_at,
        has_team_present=show.get("avpType") == _TEAM_PRESENT_AVP_TYPE,
        description=synopsis if isinstance(synopsis, str) else None,
    )
    source_url = show.get("linkShow")
    return ScreeningEvent.from_extracted(
        extracted,
        source=Source.PREMIERE_PROJO,
        source_url=source_url if isinstance(source_url, str) else _INDEX_URL,
    )


def _parse_datetime(value: object) -> datetime | None:
    """Parse an ISO 8601 timestamp into a UTC datetime, or None if invalid."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        return None
