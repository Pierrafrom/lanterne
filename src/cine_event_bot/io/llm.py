"""Structured event extraction from scraped text via an LLM.

Turns the free-text announcement of a single screening (already isolated by a
scraper) into a validated :class:`ExtractedEvent`. The LLM is reached through
Instructor over Ollama's OpenAI-compatible endpoint, using JSON mode since that
endpoint does not reliably support tool/function calling.
"""

import instructor
from openai import AsyncOpenAI

from cine_event_bot.config import Settings
from cine_event_bot.core.models import ExtractedEvent

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
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
        )


def build_extractor(settings: Settings) -> EventExtractor:
    """Build an extractor wired to the Ollama endpoint from settings.

    Args:
        settings: Application settings holding the Ollama URL and model name.

    Returns:
        An :class:`EventExtractor` ready to query the configured model.
    """
    client = instructor.from_openai(
        AsyncOpenAI(base_url=f"{settings.ollama_base_url}/v1", api_key="ollama"),
        mode=instructor.Mode.JSON,
    )
    return EventExtractor(client, settings.ollama_model)
