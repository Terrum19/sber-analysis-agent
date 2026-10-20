"""Contract-тесты: заголовки HTTP ответа SSE."""

import pytest
from fastapi.testclient import TestClient

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
    monkeypatch.setattr("app.core.data.duckdb_manager.DuckDBManager", lambda path=None: db)
    monkeypatch.setattr("app.core.agent.step_executor.DuckDBManager", lambda path=None: db)
    return FakeLLM()


def test_sse_response_headers(fake_llm):
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": "h1", "question": "Тест заголовков"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers.get("cache-control") == "no-cache, no-transform"
    assert response.headers.get("x-accel-buffering") == "no"
    assert "content-encoding" not in response.headers
