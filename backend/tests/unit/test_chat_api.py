import asyncio
import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.routes.chat import iterate_in_thread
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


def assert_pings_between_events(text: str) -> None:
    """Событие «первое», пинги в паузе, done; пинг до первого события допустим (ход ещё стартует)."""
    blocks = [block for block in text.split("\n\n") if block]
    events = [block for block in blocks if block != ": ping"]
    assert len(events) == 2
    assert "первое" in events[0]
    assert events[1] == 'data: {"type":"done","data":{}}'
    assert blocks[-1] == events[1]
    assert ": ping" in blocks[blocks.index(events[0]) + 1 : -1]


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
    assert_pings_between_events(response.text)


def test_chat_stream_pings_while_turn_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Синхронный вызов в ходе (запрос DuckDB, ожидание db.lock) не держит loop сервера (#189)."""

    async def blocking_run(self, payload):
        yield {"type": "error", "data": {"message": "первое"}}
        time.sleep(0.3)  # noqa: ASYNC251 — так блокирует запрос DuckDB
        yield {"type": "done"}

    monkeypatch.setattr("app.core.agent.orchestrator.AgentOrchestrator.run", blocking_run)
    monkeypatch.setattr("app.api.routes.chat.SSE_PING_INTERVAL_S", 0.05)
    client = TestClient(app)
    response = client.post("/api/chat", json={"session_id": "s1", "question": "Что в данных?"})
    assert_pings_between_events(response.text)


async def test_iterate_in_thread_keeps_order_and_runs_in_other_thread() -> None:
    threads = []

    async def source():
        for item in range(3):
            threads.append(threading.get_ident())
            yield item

    assert [item async for item in iterate_in_thread(source)] == [0, 1, 2]
    assert threading.get_ident() not in threads


async def test_iterate_in_thread_raises_source_error() -> None:
    async def source():
        yield 1
        raise ValueError("сбой хода")

    items = []
    with pytest.raises(ValueError, match="сбой хода"):
        async for item in iterate_in_thread(source):
            items.append(item)
    assert items == [1]


async def test_iterate_in_thread_cancels_source_when_consumer_leaves() -> None:
    closed = threading.Event()

    async def source():
        try:
            yield 1
            await asyncio.sleep(10)
            yield 2
        finally:
            closed.set()

    stream = iterate_in_thread(source)
    assert await anext(stream) == 1
    await asyncio.wait_for(stream.aclose(), timeout=2)
    assert closed.is_set()


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
