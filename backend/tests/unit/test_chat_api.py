import asyncio
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


def test_chat_stream_accepts_recovery_response() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={
            "session_id": "s1",
            "question": "Повторный запрос",
            "recovery_response": '{"action": "skip_step", "text": "Пропустить этот шаг"}',
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("data: ")


def test_chat_stream_ends_with_done_when_orchestrator_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_run(self, payload):
        raise RuntimeError("оркестратор упал")
        yield  # pragma: no cover

    monkeypatch.setattr("app.core.agent.orchestrator.AgentOrchestrator.run", broken_run)
    client = TestClient(app)
    response = client.post("/api/chat", json={"session_id": "s1", "question": "Что в данных?"})
    events = [line for line in response.text.splitlines() if line.startswith("data: ")]
    assert '"type":"error"' in events[0]
    assert "оркестратор упал" in events[0]
    assert events[-1] == 'data: {"type":"done","data":{}}'

def test_chat_stream_pings_while_orchestrator_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_run(self, payload):
        yield {"type": "error", "data": {"message": "первое"}}
        await asyncio.sleep(0.3)
        yield {"type": "done"}

    monkeypatch.setattr("app.core.agent.orchestrator.AgentOrchestrator.run", slow_run)
    monkeypatch.setattr("app.api.routes.chat.SSE_PING_INTERVAL_S", 0.05)
    client = TestClient(app)
    response = client.post("/api/chat", json={"session_id": "s1", "question": "Что в данных?"})
    blocks = [block for block in response.text.split("\n\n") if block]
    assert "первое" in blocks[0]
    assert blocks[-1] == 'data: {"type":"done","data":{}}'
    pings = blocks[1:-1]
    assert pings
    assert set(pings) == {": ping"}


def test_chat_stream_has_no_pings_without_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fast_run(self, payload):
        yield {"type": "error", "data": {"message": "первое"}}
        yield {"type": "done"}

    monkeypatch.setattr("app.core.agent.orchestrator.AgentOrchestrator.run", fast_run)
    monkeypatch.setattr("app.api.routes.chat.SSE_PING_INTERVAL_S", 5.0)
    client = TestClient(app)
    response = client.post("/api/chat", json={"session_id": "s1", "question": "Что в данных?"})
    assert ": ping" not in response.text
    assert response.text.endswith('data: {"type":"done","data":{}}\n\n')