"""Tests for the ingestion pipeline orchestration."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
from factories import make_sighting

from cine_event_bot.core.dedup import compute_dedup_key
from cine_event_bot.core.models import EventType, Film, Sighting, Source
from cine_event_bot.core.progress import ProgressReporter
from cine_event_bot.io.repository import EventRepository
from cine_event_bot.pipeline import IngestionPipeline

_MOMENT = datetime(2026, 6, 25, 18, 30, tzinfo=UTC)
_VENUE = "La Cinémathèque française"


def _sighting(source: Source, *, title: str, url: str) -> Sighting:
    return make_sighting(
        source=source,
        source_url=url,
        title=title,
        event_type=EventType.RETROSPECTIVE,
        venue=_VENUE,
        starts_at=_MOMENT,
    )


class _FakeScraper:
    def __init__(self, source: Source, sightings: list[Sighting]) -> None:
        self._source = source
        self._sightings = sightings

    @property
    def source(self) -> Source:
        return self._source

    async def fetch_events(
        self,
        client: httpx.AsyncClient,  # noqa: ARG002
        reporter: ProgressReporter,
    ) -> list[Sighting]:
        reporter.events_fetched(self._source.value, len(self._sightings))
        for _ in self._sightings:
            reporter.event_processed(self._source.value)
        return self._sightings


class _FailingScraper:
    def __init__(self, source: Source) -> None:
        self._source = source

    @property
    def source(self) -> Source:
        return self._source

    async def fetch_events(
        self,
        client: httpx.AsyncClient,  # noqa: ARG002
        reporter: ProgressReporter,  # noqa: ARG002
    ) -> list[Sighting]:
        raise httpx.ConnectError("boom")


class _NullEnricher:
    async def enrich(self, film: Film) -> None:  # noqa: ARG002
        return


class _StubEnricher:
    def __init__(self) -> None:
        self.calls = 0

    async def enrich(self, film: Film) -> None:
        self.calls += 1
        film.tmdb_id = 42


class _FailingEnricher:
    async def enrich(self, film: Film) -> None:  # noqa: ARG002
        raise httpx.ConnectError("tmdb down")


async def _count(repository: EventRepository) -> int:
    window = await repository.list_between(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)
    )
    return len(window)


async def test_run_persists_events_from_every_source(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE, [_sighting(Source.CINEMATHEQUE, title="A", url="a")]
        ),
        _FakeScraper(
            Source.FORUM_DES_IMAGES,
            [_sighting(Source.FORUM_DES_IMAGES, title="B", url="b")],
        ),
    ]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    report = await pipeline.run(MagicMock())

    assert report.events_ingested == 2
    assert report.sources_failed == 0
    assert await _count(repository) == 2


async def test_run_deduplicates_same_screening_across_sources(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    # Same title/venue/start time from two sources -> same dedup key -> one row.
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE, [_sighting(Source.CINEMATHEQUE, title="Dune", url="a")]
        ),
        _FakeScraper(
            Source.FORUM_DES_IMAGES,
            [_sighting(Source.FORUM_DES_IMAGES, title="Dune", url="b")],
        ),
    ]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    report = await pipeline.run(MagicMock())

    assert report.events_ingested == 2
    assert await _count(repository) == 1


async def test_run_skips_a_failing_source_without_aborting(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FailingScraper(Source.PREMIERE_PROJO),
        _FakeScraper(
            Source.CINEMATHEQUE, [_sighting(Source.CINEMATHEQUE, title="A", url="a")]
        ),
    ]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    report = await pipeline.run(MagicMock())

    assert report.sources_failed == 1
    assert report.events_ingested == 1
    assert await _count(repository) == 1


async def test_run_enriches_the_ingested_film(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE, [_sighting(Source.CINEMATHEQUE, title="A", url="a")]
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _StubEnricher())

    await pipeline.run(MagicMock())
    stored = await repository.get_by_dedup_key(compute_dedup_key("A", _VENUE, _MOMENT))

    assert stored is not None
    assert stored.film.tmdb_id == 42


async def test_run_enriches_a_film_only_once(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    # Two screenings of the same film -> one shared film row -> one enrichment.
    later = make_sighting(
        source=Source.CINEMATHEQUE,
        title="A",
        event_type=EventType.RETROSPECTIVE,
        venue=_VENUE,
        starts_at=datetime(2026, 6, 26, 18, 30, tzinfo=UTC),
    )
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE,
            [_sighting(Source.CINEMATHEQUE, title="A", url="a"), later],
        )
    ]
    enricher = _StubEnricher()

    await IngestionPipeline(scrapers, repository, enricher).run(MagicMock())

    assert enricher.calls == 1


async def test_run_reports_progress_per_source(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FailingScraper(Source.PREMIERE_PROJO),
        _FakeScraper(
            Source.CINEMATHEQUE,
            [
                _sighting(Source.CINEMATHEQUE, title="A", url="a"),
                _sighting(Source.CINEMATHEQUE, title="B", url="b"),
            ],
        ),
    ]
    events: list[tuple[str, object]] = []

    class _RecordingReporter:
        def source_started(self, source: str) -> None:
            events.append(("started", source))

        def events_fetched(self, source: str, total: int) -> None:  # noqa: ARG002
            events.append(("fetched", total))

        def event_processed(self, source: str) -> None:
            events.append(("processed", source))

        def source_finished(self, source: str, count: int) -> None:  # noqa: ARG002
            events.append(("finished", count))

        def source_failed(self, source: str) -> None:
            events.append(("failed", source))

    await IngestionPipeline(scrapers, repository, _NullEnricher()).run(
        MagicMock(), _RecordingReporter()
    )

    assert ("failed", "premiereprojo.fr") in events
    assert ("fetched", 2) in events
    assert events.count(("processed", "cinematheque.fr")) == 2
    assert ("finished", 2) in events


async def test_run_persists_event_even_when_enrichment_fails(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE, [_sighting(Source.CINEMATHEQUE, title="A", url="a")]
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _FailingEnricher())

    report = await pipeline.run(MagicMock())

    assert report.events_ingested == 1
    assert await _count(repository) == 1
