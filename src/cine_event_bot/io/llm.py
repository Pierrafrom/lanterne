"""Structured LLM tasks over Ollama: event extraction and question parsing.

Both turn free text into a validated Pydantic model through Instructor over
Ollama's OpenAI-compatible endpoint, using JSON mode since that endpoint does
not reliably support tool/function calling. :class:`EventExtractor` structures a
scraped announcement into an :class:`ExtractedEvent`; :class:`QuestionInterpreter`
turns a user's natural-language question into a :class:`QueryCriteria`.
"""

from datetime import date

import instructor
from openai import AsyncOpenAI

from cine_event_bot.config import Settings
from cine_event_bot.core.models import ExtractedEvent
from cine_event_bot.core.qa import QueryCriteria

# One reformatting retry on a validation error, then give up: a weak local model
# that produces an invalid value (e.g. a sentence in an enum field) rarely fixes
# it within a few tries, so more retries just multiply the slow LLM calls.
_MAX_RETRIES = 1

_SYSTEM_PROMPT = (
    "You extract structured data about a single special cinema screening in "
    "the Paris region from the announcement text given by the user. "
    "Classify event_type as one of: avant_premiere (preview, especially with "
    "the film team present), cine_concert (film with live music), "
    "retrospective (heritage/repertory cycle), open_air (outdoor screening). "
    "Set has_team_present to true only when the text states that the director "
    "or cast attend. Parse the screening date and time into an absolute UTC "
    "datetime; if the text gives a day and month without a year, use the "
    "reference date stated in the text to pick the next upcoming occurrence. "
    "Use the exact venue name as written."
)


class EventExtractor:
    """Extracts one :class:`ExtractedEvent` from an announcement's text."""

    def __init__(self, client: instructor.AsyncInstructor, model: str) -> None:
        """Bind the extractor to an instructor client and target model.

        Args:
            client: Async Instructor client wrapping the LLM endpoint.
            model: Name of the model to query (e.g. ``llama3.2``).
        """
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        """Name of the model this extractor queries."""
        return self._model

    async def extract(self, raw_text: str) -> ExtractedEvent:
        """Extract a structured screening from a raw announcement.

        Args:
            raw_text: The announcement text for one screening, as scraped.

        Returns:
            The validated screening data parsed from the text.
        """
        return await self._client.chat.completions.create(
            model=self._model,
            response_model=ExtractedEvent,
            max_retries=_MAX_RETRIES,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
        )


_QA_SYSTEM_PROMPT = (
    "You translate a user's French question about upcoming special cinema "
    "screenings into a structured filter. Leave a field null when the question "
    "does not constrain it. text_query holds film/director/keyword terms to "
    "match (or null for a broad question). event_type is one of avant_premiere, "
    "cine_concert, retrospective, open_air, or null. Set team_only to true only "
    "if the user asks for screenings with the film team present. Resolve "
    'relative time phrases ("this weekend", "soon", "tonight") into '
    "absolute UTC starts_after/starts_before bounds using the reference date "
    "given in the message."
)


class QuestionInterpreter:
    """Turns a natural-language question into a :class:`QueryCriteria`."""

    def __init__(self, client: instructor.AsyncInstructor, model: str) -> None:
        """Bind the interpreter to an instructor client and target model.

        Args:
            client: Async Instructor client wrapping the LLM endpoint.
            model: Name of the model to query (e.g. ``llama3.2``).
        """
        self._client = client
        self._model = model

    async def interpret(self, question: str, reference_date: date) -> QueryCriteria:
        """Parse a question into a structured query filter.

        Args:
            question: The user's natural-language question.
            reference_date: Date relative phrases are resolved against.

        Returns:
            The structured filter to run against the repository.
        """
        content = f"Reference date: {reference_date.isoformat()}\nQuestion: {question}"
        return await self._client.chat.completions.create(
            model=self._model,
            response_model=QueryCriteria,
            max_retries=_MAX_RETRIES,
            messages=[
                {"role": "system", "content": _QA_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        )


def _build_instructor(settings: Settings) -> instructor.AsyncInstructor:
    """Build the async Instructor client for the configured Ollama endpoint."""
    return instructor.from_openai(
        AsyncOpenAI(base_url=f"{settings.ollama_base_url}/v1", api_key="ollama"),
        mode=instructor.Mode.JSON,
    )


def build_extractor(settings: Settings) -> EventExtractor:
    """Build an extractor wired to the Ollama endpoint from settings.

    Args:
        settings: Application settings holding the Ollama URL and model name.

    Returns:
        An :class:`EventExtractor` ready to query the configured model.
    """
    return EventExtractor(_build_instructor(settings), settings.ollama_model)


def build_interpreter(settings: Settings) -> QuestionInterpreter:
    """Build a question interpreter wired to the Ollama endpoint from settings.

    Args:
        settings: Application settings holding the Ollama URL and model name.

    Returns:
        A :class:`QuestionInterpreter` ready to query the configured model.
    """
    return QuestionInterpreter(_build_instructor(settings), settings.ollama_model)
