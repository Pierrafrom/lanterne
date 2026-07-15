"""Stateless parsing helpers for the Paris Ciné Info scraper.

Split out of :mod:`lanterne.io.scrapers.paris_cine_info` — these functions
never touch scraper state (no ``self``, no HTTP calls), covering three
independent parsing concerns: a film's pre-aggregated ratings
(:func:`parse_film_ratings`), a venue's accepted subscription cards
(:func:`parse_venue_passes`), and a venue's address/room details
(:func:`parse_venue_detail`). Kept together with the scraper's core showtime
flow only through re-exported imports, so ``paris_cine_info.py`` itself
stays under the project's file-size guideline.
"""

import json
from typing import Any

from lanterne.core.models import RatingSource
from lanterne.io.scrapers.base import RatingRecord, VenueDetail, scan_balanced

# Stable anchor for the venue-passes catalogue embedded in the homepage's
# inline <script> — a hand-named JS variable, not a generated identifier.
_CINE_OPTIONS_MARKER = "const cineOptions = "


def parse_film_ratings(
    movie: dict[str, Any],
) -> tuple[str, list[RatingRecord]] | None:
    """Map one ``get_movies.php`` entry to its IMDb id and known ratings.

    Args:
        movie: One entry from ``get_movies.php``'s ``data`` array.

    Returns:
        The film's IMDb id (``"tt"`` followed by ``movie["i_id"]``) paired
        with its non-empty ratings, or None when the entry carries no IMDb
        id to key on.
    """
    raw_imdb_id = movie.get("i_id")
    if not isinstance(raw_imdb_id, str) or not raw_imdb_id:
        return None
    imdb_id = f"tt{raw_imdb_id}"

    records = [
        record
        for record in (
            _rating(
                RatingSource.IMDB,
                movie.get("im_r"),
                f"https://www.imdb.com/title/{imdb_id}/",
            ),
            _rating(RatingSource.ALLOCINE_PRESS, movie.get("ap_r"), None),
            _rating(RatingSource.ALLOCINE_AUDIENCE, movie.get("as_r"), None),
            _rating(
                RatingSource.SENSCRITIQUE,
                movie.get("sc_r"),
                _senscritique_url(movie.get("sc_u")),
            ),
            _rating(
                RatingSource.ROTTEN_TOMATOES,
                movie.get("rt_r"),
                _rotten_tomatoes_url(movie.get("rt_u")),
            ),
            _rating(
                RatingSource.METACRITIC,
                movie.get("mc_r"),
                _metacritic_url(movie.get("mc_u")),
            ),
            _rating(
                RatingSource.LETTERBOXD,
                movie.get("lb_r"),
                _letterboxd_url(movie.get("lb_u")),
            ),
        )
        if record is not None
    ]
    return imdb_id, records


def _rating(
    source: RatingSource, value: object, url: str | None
) -> RatingRecord | None:
    """Build a rating record when ``value`` is a usable positive score.

    ``get_movies.php`` represents "no data for this source" as ``0``
    (confirmed live: a missing Allociné press score is ``0`` alongside a
    populated audience score on the same entry) rather than omitting the
    field or using ``null`` — filtered out here rather than persisted as a
    real zero rating.
    """
    if not isinstance(value, int | float) or value <= 0:
        return None
    return RatingRecord(source=source, rating=float(value), url=url)


def _senscritique_url(slug: object) -> str | None:
    """Build a SensCritique film URL from the API's ``sc_u`` slug field."""
    return (
        f"https://www.senscritique.com/film/{slug}"
        if isinstance(slug, str) and slug
        else None
    )


def _rotten_tomatoes_url(path: object) -> str | None:
    """Build a Rotten Tomatoes film URL from the API's ``rt_u`` path field."""
    return (
        f"https://www.rottentomatoes.com{path}"
        if isinstance(path, str) and path
        else None
    )


def _metacritic_url(path: object) -> str | None:
    """Build a Metacritic film URL from the API's ``mc_u`` path field."""
    return (
        f"https://www.metacritic.com/movie{path}/"
        if isinstance(path, str) and path
        else None
    )


def _letterboxd_url(slug: object) -> str | None:
    """Build a Letterboxd film URL from the API's ``lb_u`` slug field."""
    return (
        f"https://letterboxd.com/film/{slug}/"
        if isinstance(slug, str) and slug
        else None
    )


def parse_venue_passes(html: str) -> dict[str, list[str]]:
    """Parse the homepage's ``cineOptions`` catalogue into venue -> card codes.

    Anchors on the ``const cineOptions = `` marker (a stable, hand-named JS
    variable) rather than a generated class or position, then scans the
    following bracket-balanced array and parses it as JSON — the array is
    department-grouped (``[{"options": [{"label", "cards": [...]}]}]``), so
    every department's cinemas are flattened into one flat mapping.

    Args:
        html: HTML of the authenticated paris-cine.info homepage (``GET /``).

    Returns:
        A mapping of venue display name to its accepted card codes; a venue
        with no accepted card, a malformed catalogue, or a missing marker
        yields an empty mapping rather than raising.
    """
    marker_index = html.find(_CINE_OPTIONS_MARKER)
    if marker_index == -1:
        return {}
    array_start = html.find("[", marker_index)
    if array_start == -1:
        return {}
    array_text = scan_balanced(html, array_start, open_char="[", close_char="]")
    if array_text is None:
        return {}
    try:
        departments = json.loads(array_text)
    except json.JSONDecodeError:
        return {}
    return dict(_flatten_venue_passes(departments))


def _flatten_venue_passes(departments: object) -> list[tuple[str, list[str]]]:
    """Flatten the department-grouped catalogue into (venue name, cards) pairs."""
    if not isinstance(departments, list):
        return []
    return [
        pair
        for department in departments
        if isinstance(department, dict)
        for pair in _department_venue_passes(department)
    ]


def _department_venue_passes(
    department: dict[str, Any],
) -> list[tuple[str, list[str]]]:
    """Return one department's (venue name, cards) pairs, skipping empty ones."""
    pairs: list[tuple[str, list[str]]] = []
    for cinema in department.get("options") or []:
        if not isinstance(cinema, dict):
            continue
        name = cinema.get("label")
        cards = _card_codes(cinema.get("cards"))
        if isinstance(name, str) and cards:
            pairs.append((name, cards))
    return pairs


def _card_codes(cards: object) -> list[str]:
    """Extract the ``cardname`` of every well-formed card entry."""
    if not isinstance(cards, list):
        return []
    codes = []
    for card in cards:
        if isinstance(card, dict):
            code = card.get("cardname")
            if isinstance(code, str):
                codes.append(code)
    return codes


def parse_venue_detail(theater: dict[str, Any], *, include_room: bool) -> VenueDetail:
    """Map one ``get_pcitheatre.php`` theater entry to a :class:`VenueDetail`.

    ``address``/``website`` are cinema-level and always mapped when present.
    ``seat_count``/``screen_width_m``/``screen_height_m`` are room-level —
    only mapped when ``include_room`` is true, i.e. the caller confirmed
    this venue had exactly one distinct room this run (see
    ``ParisCineInfoScraper.fetch_venue_details``).

    Args:
        theater: One entry from the endpoint's ``theater`` array.
        include_room: Whether to map the room-specific fields.

    Returns:
        The parsed detail; any individually unparseable field is left
        ``None`` rather than raising.
    """
    seat_count = _to_int(theater.get("seatCount")) if include_room else None
    screen_width_m = _to_float(theater.get("screenWidth")) if include_room else None
    screen_height_m = _to_float(theater.get("screenHeight")) if include_room else None
    return VenueDetail(
        address=_non_empty_str(theater.get("addr")),
        website=_non_empty_str(theater.get("url")),
        seat_count=seat_count,
        screen_width_m=screen_width_m,
        screen_height_m=screen_height_m,
    )


def _non_empty_str(value: object) -> str | None:
    """Return the value when it is a non-empty string, else None."""
    return value if isinstance(value, str) and value else None


def _to_int(value: object) -> int | None:
    """Parse a value (the API sends numbers as strings) as a positive int."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _to_float(value: object) -> float | None:
    """Parse a value (the API sends numbers as strings) as a positive float."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
