"""Contract-тесты: заголовки HTTP ответа SSE."""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch):
    from app.core.data.duckdb_manager import DuckDBManager
    from app.core.llm.fake_client import FakeLLM

    monkeypatch.setattr("app.core.llm.client.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.planner.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.insight.LLMClient", FakeLLM)
    db = DuckDBManager(path=":memory:")
    db.execute(
        """
        CREATE TABLE raw AS
        SELECT *
        FROM (
            VALUES
                ('Электроника', 100000, '2024-01-01'),
                ('Электроника', 150000, '2024-02-01'),
                ('Одежда', 50000, '2024-01-01'),
                ('Одежда', 75000, '2024-02-01')
        ) AS t(category, revenue, date)
        """
    )
    app.dependency_overrides[get_db] = lambda: db
    yield FakeLLM()
    app.dependency_overrides.pop(get_db)


def session(client: TestClient) -> str:
    """Сессия над таблицей raw, созданная через API."""
    return client.post("/api/sessions", json={"table": "raw"}).json()["session_id"]


def test_sse_response_headers(fake_llm):
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": session(client), "question": "Тест заголовков"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers.get("cache-control") == "no-cache, no-transform"
    assert response.headers.get("x-accel-buffering") == "no"
    assert "content-encoding" not in response.headers
