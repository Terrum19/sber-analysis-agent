import pytest
from fastapi.testclient import TestClient

from app.core.llm.fake_client import FakeLLM
from app.main import app


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """Подменяет LLMClient на FakeLLM для всех тестов этого модуля."""
    monkeypatch.setattr("app.core.llm.client.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.planner.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.insight.LLMClient", FakeLLM)
    return FakeLLM()


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
