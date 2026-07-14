"""Tests for venue-kind classification and subscription-pass labels."""

from cine_event_bot.core.models import VenueKind
from cine_event_bot.core.venues import classify_venue_kind, pass_label


def test_classifies_ugc_by_prefix() -> None:
    assert classify_venue_kind("UGC Ciné Cité Les Halles") is VenueKind.CHAIN_UGC


def test_classifies_pathe_by_prefix() -> None:
    assert classify_venue_kind("Pathé Beaugrenelle") is VenueKind.CHAIN_PATHE


def test_classifies_mk2_by_prefix() -> None:
    assert classify_venue_kind("MK2 Bastille (Beaumarchais)") is VenueKind.CHAIN_MK2


def test_classifies_cgr_as_chain_other() -> None:
    assert classify_venue_kind("CGR Paris Lilas") is VenueKind.CHAIN_OTHER


def test_classifies_known_institution_exactly() -> None:
    assert classify_venue_kind("La Cinémathèque française") is VenueKind.INSTITUTION
    assert (
        classify_venue_kind("Cinéma en plein air de La Villette")
        is VenueKind.INSTITUTION
    )


def test_classification_is_case_and_whitespace_insensitive() -> None:
    assert classify_venue_kind("  ugc   ciné cité les halles ") is VenueKind.CHAIN_UGC


def test_classifies_independent_by_default() -> None:
    assert classify_venue_kind("Le Champo") is VenueKind.INDEPENDENT
    assert classify_venue_kind("Le Grand Rex") is VenueKind.INDEPENDENT


def test_pass_label_returns_the_known_french_label() -> None:
    assert pass_label("ugc") == "UGC Illimité"
    assert pass_label("pass") == "Pathé CinéPass"


def test_pass_label_falls_back_to_the_raw_code_for_an_unknown_pass() -> None:
    assert pass_label("mystery-card") == "mystery-card"
