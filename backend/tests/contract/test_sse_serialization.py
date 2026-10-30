"""Contract-тесты: формат SSE сериализации."""

import json

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


def parse_events(response) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_sse_compact_json_no_spaces(fake_llm):
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": session(client), "question": "Тест SSE"},
    )
    text = response.text
    assert "data: " in text
    assert '"type":"text"' in text
    assert '"type":"chart"' in text
    assert '"type":"done"' in text


def test_sse_progress_events(fake_llm):
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": session(client), "question": "Тест SSE"},
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    progress = [event["data"] for event in events if event["type"] == "progress"]
    assert {"stage": "planning", "status": "started", "tables": ["raw"]} in progress
    assert {"stage": "insight", "status": "started"} in progress
    assert {"stage": "chart", "status": "started"} in progress
    planned = next(
        data for data in progress if data["stage"] == "planning" and data["status"] == "finished"
    )
    assert planned["total_steps"] >= 1
    finished = [
        data for data in progress if data["stage"] == "step" and data["status"] == "finished"
    ]
    assert len(finished) == planned["total_steps"]
    assert all(isinstance(data["elapsed_ms"], int) for data in finished)
    assert all(value is not None for data in progress for value in data.values())
    assert '"type":"progress"' in response.text
    assert '"stage":"planning"' in response.text
    kinds = [event["type"] for event in events]
    assert (
        kinds.index("progress") < kinds.index("text") < kinds.index("chart") < kinds.index("done")
    )


def test_sse_insight_stream_sends_chunks_before_chart(fake_llm, monkeypatch):
    from app.config import settings
    from app.core.llm.fake_client import INSIGHT_TEXT

    monkeypatch.setattr(settings, "insight_stream", True)
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": session(client), "question": "Тест стрима"},
    )
    events = parse_events(response)
    texts = [event for event in events if event["type"] == "text"]

    assert len(texts) > 1
    assert all(set(event["data"]) == {"chunk"} for event in texts)
    assert "".join(event["data"]["chunk"] for event in texts) == INSIGHT_TEXT

    chart = next(event for event in events if event["type"] == "chart")
    assert chart["data"]["caption"] == "Исследование завершено"

    kinds = [event["type"] for event in events]
    last_text = max(index for index, kind in enumerate(kinds) if kind == "text")
    assert last_text < kinds.index("chart")
    assert "text" not in kinds[kinds.index("chart") :]
    assert events[-1]["type"] == "done"


def test_sse_insight_stream_off_keeps_single_text_event(fake_llm, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "insight_stream", False)
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": session(client), "question": "Тест без стрима"},
    )
    events = parse_events(response)
    texts = [event for event in events if event["type"] == "text"]

    assert len(texts) == 1
    assert texts[0]["data"] == {
        "headline": "Исследование завершено",
        "details": ["Данные обработаны по запросу пользователя"],
        "limits": "Результаты основаны на доступных в таблице данных",
    }
    assert "chunk" not in texts[0]["data"]
    assert [event["type"] for event in events if event["type"] != "progress"] == [
        "text",
        "chart",
        "done",
    ]


def test_sse_insight_stream_fallback_sends_headline(fake_llm, monkeypatch):
    from app.config import settings
    from app.core.llm.fake_client import FakeLLM

    class FailingStreamLLM(FakeLLM):
        async def complete_stream(self, system, user, **kwargs):
            raise RuntimeError("нет стрима")
            yield ""  # pragma: no cover

    monkeypatch.setattr(settings, "insight_stream", True)
    monkeypatch.setattr("app.core.agent.insight.LLMClient", FailingStreamLLM)
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": session(client), "question": "Тест фолбэка"},
    )
    events = parse_events(response)
    texts = [event for event in events if event["type"] == "text"]

    assert len(texts) == 1
    assert texts[0]["data"] == {
        "headline": "Исследование завершено",
        "details": ["Данные обработаны по запросу пользователя"],
        "limits": "Результаты основаны на доступных в таблице данных",
    }
    assert "chunk" not in texts[0]["data"]
    assert [event["type"] for event in events if event["type"] != "progress"] == [
        "text",
        "chart",
        "done",
    ]
