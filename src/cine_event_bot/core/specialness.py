"""Rule-based specialness classification for ordinary screenings.

A width source (Paris Ciné Info's uncommented showtimes, offi.fr) reports
every screening at a venue with no signal of its own about whether it is
worth surfacing — see ``docs/decisions/0008-drop-allocine-width-source.md``
and ``docs/coverage-matrix.md``. :func:`classify_specialness` upgrades such a
screening's ``is_special`` flag from a small set of deterministic, cheap
signals: confirmed team presence, an announced cycle, an institution venue,
a film old enough that its re-appearance in cinemas is a repertory screening
rather than an ordinary theatrical run, a film shown at very few distinct
venues (a rarity/reach signal — see ADR 0008's note that a film's venue
count is a strong specialness proxy), or a (film, venue) pair with very few
showings (a one-off-slot signal, as opposed to a normal multi-showing daily
run).

The first four rules read only the event's own already-loaded ``film`` and
``venue``; the last two need aggregate counts across *all* of a film's
screenings, which no single :class:`ScreeningEvent
<cine_event_bot.core.models.ScreeningEvent>` carries — see
:class:`FilmContext` below.

Deliberately rules-only, no LLM fallback: an LLM only adds value over
free-text a rule cannot parse, and neither width source captures any free
text for its ordinary screenings today (offi.fr never scrapes any; Paris
Ciné Info's blank-``com`` showtimes carry none by definition — see
``build_showtime_item`` in ``io/scrapers/paris_cine_info.py``). Building a
classification path with no real input would be speculative infrastructure —
see ``docs/decisions/0009-specialness-rules-only.md`` for the full reasoning
and what would justify adding an LLM fallback later.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from cine_event_bot.core.models import ScreeningEvent, VenueKind

# A film re-appearing in cinemas this many years after its release is a
# repertory/classic screening, not an ordinary theatrical run (which lasts
# weeks, not years) — a heuristic, not a certainty, hence the reduced
# confidence below rather than the other rules' full certainty.
_REPERTORY_AGE_YEARS = 3

# A film showing at this many distinct venues or fewer, this week, reads as
# arthouse/limited release rather than a mainstream chain rollout.
_RARE_VENUE_COUNT_MAX = 3

# A (film, venue) pair with this many showings or fewer, this week, reads as
# a one-off slot (a ciné-club screening, a single avant-première) rather
# than a normal multi-showing daily run.
_SPARSE_SHOWING_COUNT_MAX = 2

_CERTAIN_CONFIDENCE = 1.0
_REPERTORY_CONFIDENCE = 0.7
_RARE_VENUE_CONFIDENCE = 0.6
# The noisiest rule: a normal small independent release can also have few
# slots, so a low showing count alone is the weakest of the six signals —
# the first candidate to revisit once the eval harness has real data.
_SPARSE_SHOWING_CONFIDENCE = 0.5


@dataclass(frozen=True, slots=True)
class FilmContext:
    """Aggregate signals about a film's stored screenings, external to one event.

    Computed by the caller (see ``pipeline.py``'s ``_film_context``) via
    ``EventRepository.count_distinct_venues``/``count_screenings`` — kept as
    an explicit input rather than a query inside this module so
    :func:`classify_specialness` stays a pure, DB-free function. No explicit
    date window: the database only ever holds the currently relevant
    ordinary screenings (pruned after 14 days past their start — see
    ``prune-db``) plus whatever the scrapers' own near-term horizon
    discovered, so counting everything currently stored already
    approximates "this film's current footprint" without needing a second,
    wall-clock-dependent window concept.

    Attributes:
        distinct_venue_count: Number of distinct venues with a stored
            screening of this film.
        weekly_showing_count: Number of stored screenings of this film at
            this specific event's venue.
    """

    distinct_venue_count: int
    weekly_showing_count: int


@dataclass(frozen=True, slots=True)
class SpecialnessVerdict:
    """The outcome of classifying one ordinary screening.

    Attributes:
        is_special: Whether at least one rule fired.
        reasons: Which rule(s) fired, sorted, or None when none did.
        confidence: The strongest fired rule's confidence, or None when none did.
    """

    is_special: bool
    reasons: list[str] | None
    confidence: float | None


def classify_specialness(
    event: ScreeningEvent, context: FilmContext
) -> SpecialnessVerdict:
    """Classify an ordinary screening from its own facts plus its film's weekly context.

    Args:
        event: The screening to classify, with its ``film`` and ``venue``
            relationships loaded. Its own current ``is_special`` value is
            ignored — callers only invoke this for a screening not already
            flagged special (see ``pipeline.py``'s ``_classify_specialness``).
        context: Aggregate counts of this film's screenings this week.

    Returns:
        A special verdict with its reasons and confidence when at least one
        rule fired, otherwise a non-special verdict with no reasons.
    """
    fired = dict(_fired_rules(event, context))
    if not fired:
        return SpecialnessVerdict(is_special=False, reasons=None, confidence=None)
    return SpecialnessVerdict(
        is_special=True, reasons=sorted(fired), confidence=max(fired.values())
    )


def _fired_rules(
    event: ScreeningEvent, context: FilmContext
) -> Iterator[tuple[str, float]]:
    """Yield ``(reason, confidence)`` for every rule that fires on this screening."""
    if event.has_team_present:
        yield "team_present", _CERTAIN_CONFIDENCE
    if event.cycle_name is not None:
        yield "cycle_name", _CERTAIN_CONFIDENCE
    if event.venue.kind is VenueKind.INSTITUTION:
        yield "institution_venue", _CERTAIN_CONFIDENCE
    release_year = event.film.release_year
    if (
        release_year is not None
        and event.starts_at.year - release_year >= _REPERTORY_AGE_YEARS
    ):
        yield "repertory_rarity", _REPERTORY_CONFIDENCE
    if context.distinct_venue_count <= _RARE_VENUE_COUNT_MAX:
        yield "rare_venue_count", _RARE_VENUE_CONFIDENCE
    if context.weekly_showing_count <= _SPARSE_SHOWING_COUNT_MAX:
        yield "sparse_showing_frequency", _SPARSE_SHOWING_CONFIDENCE
