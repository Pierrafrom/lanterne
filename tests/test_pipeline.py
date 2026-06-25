"""Tests for the ingestion pipeline orchestration."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx

from cine_event_bot.core.models import EventType, ExtractedEvent, ScreeningEvent, Source
from cine_event_bot.io.repository import EventRepository
from cine_event_bot.pipeline import IngestionPipeline


def _event(source: Source, *, title: str, url: str) -> ScreeningEvent:
    extracted = ExtractedEvent(
        title=title,
        event_type=EventType.RETROSPECTIVE,
        venue="La Cinémathèque française",
        starts_at=datetime(2026, 6, 25, 18, 30, tzinfo=UTC),
    )
    return ScreeningEvent.from_extracted(extracted, source=source, source_url=url)


class _FakeScraper:
    def __init__(self, source: Source, events: list[ScreeningEvent]) -> None:
        self._source = source
        self._events = events

    @property
    def source(self) -> Source:
        return self._source

    async def fetch_events(self, client: httpx.AsyncClient) -> list[ScreeningEvent]:  # noqa: ARG002
        return self._events


class _FailingScraper:
    def __init__(self, source: Source) -> None:
        self._source = source

    @property
    def source(self) -> Source:
        return self._source

    async def fetch_events(self, client: httpx.AsyncClient) -> list[ScreeningEvent]:  # noqa: ARG002
        raise httpx.ConnectError("boom")


async def _count(repository: EventRepository) -> int:
    window = await repository.list_between(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)
    )
    return len(window)


async def test_run_persists_events_from_every_source(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE, [_event(Source.CINEMATHEQUE, title="A", url="a")]
        ),
        _FakeScraper(
            Source.FORUM_DES_IMAGES,
            [_event(Source.FORUM_DES_IMAGES, title="B", url="b")],
        ),
    ]
    pipeline = IngestionPipeline(scrapers, repository)

    report = await pipeline.run(MagicMock())

    assert report.events_ingested == 2
    assert report.sources_failed == 0
    assert await _count(repository) == 2


async def test_run_deduplicates_same_screening_across_sources(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    # Same title/venue/start time from two sources -> same dedup key -> one row.
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE, [_event(Source.CINEMATHEQUE, title="Dune", url="a")]
        ),
        _FakeScraper(
            Source.SORTIRAPARIS, [_event(Source.SORTIRAPARIS, title="Dune", url="b")]
        ),
    ]
    pipeline = IngestionPipeline(scrapers, repository)

    report = await pipeline.run(MagicMock())

    assert report.events_ingested == 2
    assert await _count(repository) == 1


async def test_run_skips_a_failing_source_without_aborting(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FailingScraper(Source.PREMIERE_PROJO),
        _FakeScraper(
            Source.CINEMATHEQUE, [_event(Source.CINEMATHEQUE, title="A", url="a")]
        ),
    ]
    pipeline = IngestionPipeline(scrapers, repository)

    report = await pipeline.run(MagicMock())

    assert report.sources_failed == 1
    assert report.events_ingested == 1
    assert await _count(repository) == 1
