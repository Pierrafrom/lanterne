"""Tests for the domain models (enums, extraction contract, sightings)."""

import pytest
from factories import make_extracted, make_sighting
from pydantic import ValidationError

from cine_event_bot.core.models import EventType, Film, Source, Venue


def test_source_enum_carries_domain_url() -> None:
    assert Source.PREMIERE_PROJO.value == "premiereprojo.fr"
    assert Source.FORUM_DES_IMAGES.value == "forumdesimages.fr"


def test_extracted_event_requires_core_fields() -> None:
    extracted = make_extracted(has_team_present=True)

    assert extracted.event_type is EventType.AVANT_PREMIERE
    assert extracted.has_team_present is True
    assert extracted.cycle_name is None


def test_extracted_event_treats_null_team_flag_as_false() -> None:
    extracted = make_extracted(has_team_present=None)

    assert extracted.has_team_present is False


def test_sighting_carries_extraction_and_provenance() -> None:
    sighting = make_sighting(
        source=Source.CINEMATHEQUE,
        source_url="https://www.cinematheque.fr/seance/1.html",
    )

    assert sighting.extracted.title == "Dune"
    assert sighting.source is Source.CINEMATHEQUE
    assert sighting.booking_url is None


def test_sighting_is_immutable() -> None:
    sighting = make_sighting()

    with pytest.raises(ValidationError):
        sighting.source = Source.CINEMATHEQUE  # type: ignore[misc] — asserting frozen behavior


def test_film_starts_without_enrichment() -> None:
    film = Film(title_key="dune", title="Dune")

    assert film.tmdb_id is None
    assert film.director is None
    assert film.genres is None
    assert film.vote_average is None


def test_venue_starts_without_enrichment() -> None:
    venue = Venue(slug="le grand rex", name="Le Grand Rex")

    assert venue.address is None
    assert venue.website is None
