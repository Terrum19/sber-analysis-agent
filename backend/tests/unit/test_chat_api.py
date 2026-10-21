from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.main import app


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeLLM]:
    """Подменяет LLMClient на FakeLLM и DuckDB приложения на пустую in-memory базу."""
    monkeypatch.setattr("app.core.llm.client.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.planner.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.insight.LLMClient", FakeLLM)
    db = DuckDBManager(":memory:")
    app.dependency_overrides[get_db] = lambda: db
    yield FakeLLM()
    app.dependency_overrides.pop(get_db)


def test_chat_stream_forbids_proxy_buffering() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": "s1", "question": "Что в данных?"},
        headers={"Accept-Encoding": "gzip"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert "content-encoding" not in response.headers
    assert response.text.startswith("data: ")
    assert '"type":"error"' in response.text
