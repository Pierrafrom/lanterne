"""Tests for the LLM-backed event extractor (instructor client mocked)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from cine_event_bot.config import Settings
from cine_event_bot.core.models import EventType, ExtractedEvent
from cine_event_bot.io.llm import (
    _QA_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
    EventExtractor,
    build_extractor,
)


def _expected() -> ExtractedEvent:
    return ExtractedEvent(
        title="Dune: Part Two",
        event_type=EventType.AVANT_PREMIERE,
        venue="Le Grand Rex",
        # Naive: the model is asked for the local Paris digits verbatim, not
        # a self-converted UTC value — see EventExtractor.extract.
        starts_at=datetime(2026, 7, 1, 20, 30),
        has_team_present=True,
    )


def _mock_client(returns: ExtractedEvent) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=returns)
    return client


async def test_extract_returns_structured_event() -> None:
    expected = _expected()
    extractor = EventExtractor(_mock_client(expected), model="llama3.2")

    result = await extractor.extract("Avant-première de Dune au Grand Rex...")

    assert result.title == expected.title
    assert result.event_type == expected.event_type
    assert result.venue == expected.venue
    assert result.has_team_present == expected.has_team_present


async def test_extract_converts_the_model_s_local_paris_reading_to_utc() -> None:
    """The model reads local digits verbatim; extract() converts to UTC.

    12 July is CEST (UTC+2) — 20:30 local becomes 18:30 UTC.
    """
    extractor = EventExtractor(_mock_client(_expected()), model="llama3.2")

    result = await extractor.extract("raw announcement text")

    assert result.starts_at == datetime(2026, 7, 1, 18, 30, tzinfo=UTC)


async def test_extract_ignores_any_tzinfo_the_model_attaches_anyway() -> None:
    """A model that ignores the no-conversion instruction is overridden.

    Whatever tzinfo the model attaches is discarded — the reading is always
    anchored on Europe/Paris, never trusted as already-UTC.
    """
    mistaken = _expected().model_copy(
        update={"starts_at": datetime(2026, 7, 1, 20, 30, tzinfo=UTC)}
    )
    extractor = EventExtractor(_mock_client(mistaken), model="llama3.2")

    result = await extractor.extract("raw announcement text")

    assert result.starts_at == datetime(2026, 7, 1, 18, 30, tzinfo=UTC)


async def test_extract_requests_the_configured_model_and_schema() -> None:
    client = _mock_client(_expected())
    extractor = EventExtractor(client, model="llama3.2")

    await extractor.extract("raw announcement text")

    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["model"] == "llama3.2"
    assert kwargs["response_model"] is ExtractedEvent
    assert kwargs["messages"][-1]["content"] == "raw announcement text"


def test_build_extractor_wires_the_settings_model() -> None:
    settings = Settings(
        telegram_bot_token="token",  # noqa: S106 — test fixture
        tmdb_api_key="tmdb",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.2",
    )

    extractor = build_extractor(settings)

    assert isinstance(extractor, EventExtractor)
    assert extractor.model == "llama3.2"


def test_extraction_prompt_describes_every_event_type() -> None:
    for member in EventType:
        assert member.value in _SYSTEM_PROMPT


def test_qa_prompt_lists_every_event_type() -> None:
    for member in EventType:
        assert member.value in _QA_SYSTEM_PROMPT
