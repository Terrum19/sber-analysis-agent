"""E2E-сценарии с FakeLLM для проверки полного цикла без локальной модели.

Сценарии по чеклисту кейса:
1. Первичное знакомство с неизвестными данными.
2. Ответы на вопросы по содержанию.
3. Группировки и аномалии.
4. Визуализация по запросу (+ смена вида).
5. Создание дашборда + повторный доступ.
6. Диалог с контекстом.
"""

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.main import app


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeLLM]:
    """Подменяет LLMClient и DuckDB приложения на тестовые двойники."""
    monkeypatch.setattr("app.core.llm.client.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.planner.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.insight.LLMClient", FakeLLM)
    # In-memory DuckDB с тестовыми данными
    db = DuckDBManager(path=":memory:")
    db.execute(
        """
        CREATE TABLE raw AS
        SELECT category, revenue, date::DATE AS date
        FROM (
            VALUES
                ('Электроника', 100000, '2024-01-01'),
                ('Электроника', 150000, '2024-02-01'),
                ('Одежда', 50000, '2024-01-01'),
                ('Одежда', 75000, '2024-02-01'),
                ('Продукты', 200000, '2024-01-01'),
                ('Продукты', 180000, '2024-02-01')
        ) AS t(category, revenue, date)
        """
    )
    app.dependency_overrides[get_db] = lambda: db
    yield FakeLLM()
    app.dependency_overrides.pop(get_db)


def session(client: TestClient) -> str:
    """Сессия над таблицей raw, созданная через API."""
    return client.post("/api/sessions", json={"table": "raw"}).json()["session_id"]


def events(response) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in response.text.splitlines() if line]


def test_scenario_1(fake_llm: FakeLLM) -> None:
    """Сценарий 1: «что передо мной?» — объём и период из паспорта, без планировщика."""
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": session(client), "question": "Что передо мной и за какой период?"},
    )
    assert response.status_code == 200
    stream = events(response)
    assert [event["type"] for event in stream if event["type"] != "progress"] == [
        "text",
        "chart",
        "done",
    ]
    headline = stream[1]["data"]["headline"]
    assert "6 строк" in headline
    assert "с 2024-01-01 по 2024-02-01" in headline
    assert all(event["data"].get("stage") != "planning" for event in stream)


def test_scenario_simple_question(fake_llm: FakeLLM) -> None:
    """Сценарий 1-2: простой вопрос к данным."""
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": session(client), "question": "Какие значения встречаются чаще всего?"},
    )
    assert response.status_code == 200
    text = response.text
    assert '"type":"text"' in text
    assert '"type":"chart"' in text
    assert '"type":"done"' in text


def test_scenario_grouping(fake_llm: FakeLLM) -> None:
    """Сценарий 3: группировка по колонкам."""
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": session(client), "question": "Среднее значение по категории"},
    )
    assert response.status_code == 200
    text = response.text
    assert '"type":"done"' in text


def test_scenario_dashboard_request(fake_llm: FakeLLM) -> None:
    """Сценарий 5: запрос дашборда."""
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": session(client), "question": "Создай дашборд с последними данными"},
    )
    assert response.status_code == 200
    text = response.text
    assert '"type":"done"' in text


def test_scenario_dialogue_context(fake_llm: FakeLLM) -> None:
    """Сценарий 6: диалог с контекстом (два вопроса подряд)."""
    client = TestClient(app)
    session_id = session(client)
    r1 = client.post("/api/chat", json={"session_id": session_id, "question": "Первый вопрос"})
    assert r1.status_code == 200
    r2 = client.post("/api/chat", json={"session_id": session_id, "question": "Второй вопрос"})
    assert r2.status_code == 200
