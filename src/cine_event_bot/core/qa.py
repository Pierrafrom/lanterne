"""Natural-language Q&A over programmed screenings.

A question is turned by the LLM into a :class:`QueryCriteria` (the structured
filter), the repository runs it, and :func:`format_qa_answer` renders the
matches. This module holds the pure pieces — the criteria model and the French
answer formatting; the LLM interpretation lives in ``io/llm.py`` and the query
in the repository. The answer is sent with Telegram's HTML parse mode (see
``io/bot.py``), so every dynamic value is HTML-escaped and each line gets a
clickable "Réserver" link when a booking URL is known.
"""

import html
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel

from cine_event_bot.core.frenchfmt import (
    booking_link_html,
    event_type_label,
    french_date,
    french_time,
)
from cine_event_bot.core.models import EventType, ScreeningEvent

_NO_MATCH = "Je n'ai trouvé aucune séance correspondante."
_HEADER = "Voici les séances qui correspondent :"


class QueryCriteria(BaseModel):
    """Structured filter extracted from a natural-language question.

    Every field is optional: an absent field means "no constraint". Time bounds
    are absolute UTC datetimes the LLM resolves from relative phrases ("this
    weekend", "soon") against the reference date it is given.

    Attributes:
        text_query: Free text matched against the film title and synopsis.
        event_type: Restrict to a single category of screening.
        team_only: Keep only screenings attended by the film team.
        starts_after: Keep screenings starting at or after this instant.
        starts_before: Keep screenings starting strictly before this instant.
    """

    text_query: str | None = None
    event_type: EventType | None = None
    team_only: bool = False
    starts_after: datetime | None = None
    starts_before: datetime | None = None


def format_qa_answer(events: Sequence[ScreeningEvent]) -> str:
    """Render the matching screenings as a French answer.

    Args:
        events: The screenings matching the question, in display order.

    Returns:
        A short French message — the matches, or a no-result notice.
    """
    if not events:
        return _NO_MATCH
    lines = [_HEADER]
    lines.extend(_answer_line(event) for event in events)
    return "\n".join(lines)


def _answer_line(event: ScreeningEvent) -> str:
    """Format one matching screening as an HTML answer bullet line.

    Expects the event's ``film`` and ``venue`` relationships to be loaded
    (repository queries eager-load them).
    """
    title = event.film.title
    if event.film.release_year:
        title = f"{title} ({event.film.release_year})"
    label = event_type_label(event.event_type)
    suffix = " ⭐ en présence de l'équipe" if event.has_team_present else ""
    when = f"{french_date(event.starts_at)} à {french_time(event.starts_at)}"
    return (
        f"• {when} — {html.escape(title)} · {html.escape(event.venue.name)} · "
        f"{html.escape(label)}{suffix}{booking_link_html(event.booking_url)}"
    )
