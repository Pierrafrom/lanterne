"""Tests for the ingestion pipeline orchestration."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import httpx
import pytest
from factories import make_sighting

from lanterne.core.dedup import compute_dedup_key
from lanterne.core.models import EventType, Film, RatingSource, Sighting, Source
from lanterne.core.progress import ProgressReporter
from lanterne.io.repository import EventRepository
from lanterne.io.scrapers.base import RatingRecord, VenueDetail
from lanterne.pipeline import IngestionPipeline

_MOMENT = datetime(2026, 6, 25, 18, 30, tzinfo=UTC)
_VENUE = "La Cinémathèque française"
# An independent, non-institution venue — isolates the repertory-rarity rule
# in the specialness tests below from the institution_venue rule, which
# would otherwise also fire on _VENUE.
_ORDINARY_VENUE = "Le Grand Rex"


def _sighting(source: Source, *, title: str, url: str) -> Sighting:
    return make_sighting(
        source=source,
        source_url=url,
        title=title,
        event_type=EventType.RETROSPECTIVE,
        venue=_VENUE,
        starts_at=_MOMENT,
    )


def _ordinary_sighting(source: Source, *, title: str, url: str) -> Sighting:
    return make_sighting(
        source=source,
        source_url=url,
        title=title,
        event_type=None,
        venue=_ORDINARY_VENUE,
        starts_at=_MOMENT,
    )


def _sightings_across_venues(
    source: Source, *, title: str, venues: int, showings_per_venue: int
) -> list[Sighting]:
    """Build sightings of one film spread over several venues and showtimes.

    Used to push a film's aggregate venue/showing counts above the
    specialness classifier's thresholds (see ``core/specialness.py``) —
    every venue name here is independent (no chain/institution prefix), so
    only the two aggregate rules are exercised.
    """
    return [
        make_sighting(
            source=source,
            source_url=f"{title}-{venue}-{showing}",
            title=title,
            event_type=None,
            venue=f"Cinéma {venue}",
            starts_at=_MOMENT + timedelta(hours=venue * 10 + showing),
        )
        for venue in range(venues)
        for showing in range(showings_per_venue)
    ]


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


class _VenuePassScraper(_FakeScraper):
    """A scraper that also implements the optional VenuePassSource capability."""

    def __init__(
        self, source: Source, sightings: list[Sighting], passes: dict[str, list[str]]
    ) -> None:
        super().__init__(source, sightings)
        self._passes = passes

    async def fetch_venue_passes(
        self,
        client: httpx.AsyncClient,  # noqa: ARG002
    ) -> dict[str, list[str]]:
        return self._passes


class _FailingVenuePassScraper(_FakeScraper):
    """A VenuePassSource-capable scraper whose pass fetch always fails."""

    async def fetch_venue_passes(
        self,
        client: httpx.AsyncClient,  # noqa: ARG002
    ) -> dict[str, list[str]]:
        raise httpx.ConnectError("boom")


class _VenueDetailScraper(_FakeScraper):
    """A scraper that also implements the optional VenueDetailSource capability."""

    def __init__(
        self,
        source: Source,
        sightings: list[Sighting],
        details: dict[str, VenueDetail],
    ) -> None:
        super().__init__(source, sightings)
        self._details = details

    async def fetch_venue_details(
        self,
        client: httpx.AsyncClient,  # noqa: ARG002
    ) -> dict[str, VenueDetail]:
        return self._details


class _FailingVenueDetailScraper(_FakeScraper):
    """A VenueDetailSource-capable scraper whose detail fetch always fails."""

    async def fetch_venue_details(
        self,
        client: httpx.AsyncClient,  # noqa: ARG002
    ) -> dict[str, VenueDetail]:
        raise httpx.ConnectError("boom")


class _FilmRatingScraper(_FakeScraper):
    """A scraper that also implements the optional FilmRatingSource capability."""

    def __init__(
        self,
        source: Source,
        sightings: list[Sighting],
        ratings: dict[str, list[RatingRecord]],
    ) -> None:
        super().__init__(source, sightings)
        self._ratings = ratings

    async def fetch_film_ratings(
        self,
        client: httpx.AsyncClient,  # noqa: ARG002
    ) -> dict[str, list[RatingRecord]]:
        return self._ratings


class _FailingFilmRatingScraper(_FakeScraper):
    """A FilmRatingSource-capable scraper whose ratings fetch always fails."""

    async def fetch_film_ratings(
        self,
        client: httpx.AsyncClient,  # noqa: ARG002
    ) -> dict[str, list[RatingRecord]]:
        raise httpx.ConnectError("boom")


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


class _RepertoryEnricher:
    """Fills the release year an old-film screening becomes special from."""

    async def enrich(self, film: Film) -> None:
        film.tmdb_id = 42
        film.release_year = 2010


class _FailingEnricher:
    async def enrich(self, film: Film) -> None:  # noqa: ARG002
        raise httpx.ConnectError("tmdb down")


class _ImdbEnricher:
    """Fills the IMDb id a film-ratings match is keyed on."""

    async def enrich(self, film: Film) -> None:
        film.tmdb_id = 42
        film.imdb_id = "tt0055852"


class _SameTmdbIdEnricher:
    """Matches every film to the same TMDB id — a real collision at scale.

    Two differently-spelled announced titles (a subtitle variant, a source's
    own formatting) can legitimately both resolve to the same film on TMDB;
    since ``Film.tmdb_id`` is unique, the second ``save_film`` then hits a
    real ``IntegrityError`` at the database level (see the regression test
    below).
    """

    async def enrich(self, film: Film) -> None:
        film.tmdb_id = 999


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


async def test_run_upgrades_an_ordinary_screening_once_enriched(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FakeScraper(
            Source.OFFI, [_ordinary_sighting(Source.OFFI, title="Old Film", url="a")]
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _RepertoryEnricher())

    await pipeline.run(MagicMock())
    stored = await repository.get_by_dedup_key(
        compute_dedup_key("Old Film", _ORDINARY_VENUE, _MOMENT)
    )

    assert stored is not None
    assert stored.is_special is True
    # A single sighting also trivially satisfies the two aggregate rules
    # (one venue, one showing) — all three fire together here.
    assert stored.specialness_reasons == [
        "rare_venue_count",
        "repertory_rarity",
        "sparse_showing_frequency",
    ]
    assert stored.specialness_confidence == 0.7


async def test_run_leaves_an_ordinary_screening_with_no_signal_unclassified(
    session,  # noqa: ANN001
) -> None:
    repository = EventRepository(session)
    # Enough venues and showings-per-venue to clear both aggregate-rule
    # thresholds, so the checked screening carries no signal at all.
    sightings = _sightings_across_venues(
        Source.OFFI, title="Ordinary", venues=4, showings_per_venue=3
    )
    scrapers = [_FakeScraper(Source.OFFI, sightings)]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    await pipeline.run(MagicMock())
    stored = await repository.get_by_dedup_key(
        compute_dedup_key("Ordinary", "Cinéma 0", _MOMENT)
    )

    assert stored is not None
    assert stored.is_special is False
    assert stored.specialness_reasons is None


async def test_run_never_flags_a_widely_released_film_mid_ingestion(
    session,  # noqa: ANN001
) -> None:
    # Regression test for the batch-pass design: if specialness classified
    # inline right after each sighting, the first venue ingested for this
    # film would see a venue count of 1 (rare) and lock in a false positive
    # before the other venues landed. Deferring to one end-of-run pass over
    # the final counts must avoid that for every one of them.
    repository = EventRepository(session)
    sightings = _sightings_across_venues(
        Source.OFFI, title="Wide Release", venues=5, showings_per_venue=3
    )
    scrapers = [_FakeScraper(Source.OFFI, sightings)]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    await pipeline.run(MagicMock())

    for venue in range(5):
        stored = await repository.get_by_dedup_key(
            compute_dedup_key(
                "Wide Release",
                f"Cinéma {venue}",
                _MOMENT + timedelta(hours=venue * 10),
            )
        )
        assert stored is not None
        assert stored.is_special is False


async def test_run_flags_a_rarely_shown_film_via_aggregate_rules(
    session,  # noqa: ANN001
) -> None:
    repository = EventRepository(session)
    sightings = _sightings_across_venues(
        Source.OFFI, title="Niche Film", venues=2, showings_per_venue=1
    )
    scrapers = [_FakeScraper(Source.OFFI, sightings)]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    await pipeline.run(MagicMock())
    stored = await repository.get_by_dedup_key(
        compute_dedup_key("Niche Film", "Cinéma 0", _MOMENT)
    )

    assert stored is not None
    assert stored.is_special is True
    assert stored.specialness_reasons == [
        "rare_venue_count",
        "sparse_showing_frequency",
    ]


async def test_run_never_overwrites_a_curated_sources_reasons(
    session,  # noqa: ANN001
) -> None:
    repository = EventRepository(session)
    # This screening is already special by construction (curated_source); the
    # enricher would also satisfy the repertory-rarity rule, but the classifier
    # must not touch a screening that is special already.
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE, [_sighting(Source.CINEMATHEQUE, title="A", url="a")]
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _RepertoryEnricher())

    await pipeline.run(MagicMock())
    stored = await repository.get_by_dedup_key(compute_dedup_key("A", _VENUE, _MOMENT))

    assert stored is not None
    assert stored.specialness_reasons == ["curated_source"]


async def test_run_survives_a_duplicate_tmdb_id_collision_during_enrichment(
    session,  # noqa: ANN001
) -> None:
    # Regression test: a real IntegrityError (UNIQUE constraint on
    # film.tmdb_id) triggers a session rollback, which expires every
    # SQLAlchemy object the session had loaded. _enrich's exception handler
    # used to log film.title straight from the (now expired) object,
    # crashing with MissingGreenlet on the implicit lazy-reload attempt and
    # taking down the entire run — not just the one film that collided.
    repository = EventRepository(session)
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE,
            [
                _sighting(Source.CINEMATHEQUE, title="Title A", url="a"),
                _sighting(Source.CINEMATHEQUE, title="Title B", url="b"),
            ],
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _SameTmdbIdEnricher())

    report = await pipeline.run(MagicMock())

    # Both events persist; only the second film's enrichment collided and
    # was logged, it did not abort ingestion of anything else.
    assert report.events_ingested == 2


async def test_run_merges_two_titles_that_resolve_to_the_same_tmdb_film(
    session,  # noqa: ANN001
) -> None:
    # "Title A" and "Title B" are two differently-worded announcements of
    # the same real film (e.g. a punctuation variant normalize_text does
    # not unify) — both resolve to tmdb_id=999. Rather than the second one
    # losing its enrichment to a swallowed IntegrityError, its screening is
    # repointed onto the first (already-enriched) Film row and the
    # duplicate Film row is deleted.
    repository = EventRepository(session)
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE,
            [
                _sighting(Source.CINEMATHEQUE, title="Title A", url="a"),
                _sighting(Source.CINEMATHEQUE, title="Title B", url="b"),
            ],
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _SameTmdbIdEnricher())

    await pipeline.run(MagicMock())

    first = await repository.get_by_dedup_key(
        compute_dedup_key("Title A", _VENUE, _MOMENT)
    )
    second = await repository.get_by_dedup_key(
        compute_dedup_key("Title B", _VENUE, _MOMENT)
    )
    assert first is not None
    assert second is not None
    assert first.film_id == second.film_id
    assert first.film.tmdb_id == 999

    winner = await repository.find_film_by_tmdb_id(999)
    assert winner is not None
    assert winner.id == first.film_id
    assert await _count(repository) == 2


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


async def test_run_applies_venue_passes_from_a_venue_pass_capable_scraper(
    session,  # noqa: ANN001
) -> None:
    repository = EventRepository(session)
    scrapers = [
        _VenuePassScraper(
            Source.OFFI,
            [_ordinary_sighting(Source.OFFI, title="Old Film", url="a")],
            {_ORDINARY_VENUE: ["ugc", "pass"]},
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    await pipeline.run(MagicMock())
    stored = await repository.get_by_dedup_key(
        compute_dedup_key("Old Film", _ORDINARY_VENUE, _MOMENT)
    )

    assert stored is not None
    assert stored.venue.accepted_passes == ["pass", "ugc"]


async def test_run_ignores_scrapers_with_no_venue_pass_capability(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE, [_sighting(Source.CINEMATHEQUE, title="A", url="a")]
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    # Should not raise even though no scraper implements VenuePassSource.
    report = await pipeline.run(MagicMock())

    assert report.events_ingested == 1


async def test_run_survives_a_failing_venue_passes_fetch(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FailingVenuePassScraper(
            Source.OFFI, [_ordinary_sighting(Source.OFFI, title="Old Film", url="a")]
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    report = await pipeline.run(MagicMock())

    assert report.events_ingested == 1


async def test_run_applies_venue_details_from_a_venue_detail_capable_scraper(
    session,  # noqa: ANN001
) -> None:
    repository = EventRepository(session)
    scrapers = [
        _VenueDetailScraper(
            Source.OFFI,
            [_ordinary_sighting(Source.OFFI, title="Old Film", url="a")],
            {_ORDINARY_VENUE: VenueDetail(seat_count=200, screen_width_m=12.0)},
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    await pipeline.run(MagicMock())
    stored = await repository.get_by_dedup_key(
        compute_dedup_key("Old Film", _ORDINARY_VENUE, _MOMENT)
    )

    assert stored is not None
    assert stored.venue.seat_count == 200
    assert stored.venue.screen_width_m == 12.0


async def test_run_ignores_scrapers_with_no_venue_detail_capability(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE, [_sighting(Source.CINEMATHEQUE, title="A", url="a")]
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    # Should not raise even though no scraper implements VenueDetailSource.
    report = await pipeline.run(MagicMock())

    assert report.events_ingested == 1


async def test_run_survives_a_failing_venue_details_fetch(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FailingVenueDetailScraper(
            Source.OFFI, [_ordinary_sighting(Source.OFFI, title="Old Film", url="a")]
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    report = await pipeline.run(MagicMock())

    assert report.events_ingested == 1


async def test_run_applies_film_ratings_from_a_film_rating_capable_scraper(
    session,  # noqa: ANN001
) -> None:
    repository = EventRepository(session)
    scrapers = [
        _FilmRatingScraper(
            Source.OFFI,
            [_ordinary_sighting(Source.OFFI, title="Old Film", url="a")],
            {"tt0055852": [RatingRecord(source=RatingSource.IMDB, rating=7.8)]},
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _ImdbEnricher())

    await pipeline.run(MagicMock())
    stored = await repository.get_by_dedup_key(
        compute_dedup_key("Old Film", _ORDINARY_VENUE, _MOMENT)
    )

    assert stored is not None
    ratings = await repository.list_film_ratings(stored.film_id)
    assert len(ratings) == 1
    assert ratings[0].source is RatingSource.IMDB
    assert ratings[0].rating == 7.8


async def test_run_ignores_scrapers_with_no_film_rating_capability(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FakeScraper(
            Source.CINEMATHEQUE, [_sighting(Source.CINEMATHEQUE, title="A", url="a")]
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    # Should not raise even though no scraper implements FilmRatingSource.
    report = await pipeline.run(MagicMock())

    assert report.events_ingested == 1


async def test_run_survives_a_failing_film_ratings_fetch(session) -> None:  # noqa: ANN001
    repository = EventRepository(session)
    scrapers = [
        _FailingFilmRatingScraper(
            Source.OFFI, [_ordinary_sighting(Source.OFFI, title="Old Film", url="a")]
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _ImdbEnricher())

    report = await pipeline.run(MagicMock())

    assert report.events_ingested == 1


async def test_run_logs_a_specialness_verdict_for_a_fired_rule(
    session,  # noqa: ANN001
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = EventRepository(session)
    scrapers = [
        _FakeScraper(
            Source.OFFI, [_ordinary_sighting(Source.OFFI, title="Old Film", url="a")]
        )
    ]
    pipeline = IngestionPipeline(scrapers, repository, _RepertoryEnricher())

    with caplog.at_level(logging.INFO, logger="lanterne.pipeline"):
        await pipeline.run(MagicMock())

    verdicts = [r for r in caplog.records if r.message == "specialness verdict"]
    assert len(verdicts) == 1
    ctx = verdicts[0].ctx
    assert ctx["title"] == "Old Film"
    assert ctx["venue"] == _ORDINARY_VENUE
    assert ctx["is_special"] is True
    assert "repertory_rarity" in ctx["reasons"]


async def test_run_logs_a_specialness_verdict_when_no_rule_fires(
    session,  # noqa: ANN001
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = EventRepository(session)
    sightings = _sightings_across_venues(
        Source.OFFI, title="Ordinary", venues=4, showings_per_venue=3
    )
    scrapers = [_FakeScraper(Source.OFFI, sightings)]
    pipeline = IngestionPipeline(scrapers, repository, _NullEnricher())

    with caplog.at_level(logging.INFO, logger="lanterne.pipeline"):
        await pipeline.run(MagicMock())

    verdicts = [r for r in caplog.records if r.message == "specialness verdict"]
    # One verdict logged per one of the twelve ordinary screenings ingested.
    assert len(verdicts) == 12
    assert all(v.ctx["is_special"] is False for v in verdicts)
    assert all(v.ctx["reasons"] is None for v in verdicts)
