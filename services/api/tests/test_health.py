"""Tests for the /health endpoint."""

import pytest
from fastapi.testclient import TestClient

from api.main import app

_REQUIRED_ENV_VARS = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TMDB_API_KEY": "test-key",
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "OLLAMA_MODEL": "test-model",
}


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _REQUIRED_ENV_VARS.items():
        monkeypatch.setenv(name, value)


def test_health_reports_error_when_database_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://nobody:nobody@localhost:1/does-not-exist",
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert "database" in body
