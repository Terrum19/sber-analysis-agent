"""E2E-сценарии с FakeLLM для проверки полного цикла без локальной модели.

Сценарии по чеклисту кейса:
1. Первичное знакомство с неизвестными данными.
2. Ответы на вопросы по содержанию.
3. Группировки и аномалии.
4. Визуализация по запросу (+ смена вида).
5. Создание дашборда + повторный доступ.
6. Диалог с контекстом.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import StepPlanner
from app.core.agent.insight import InsightGenerator
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.main import app


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """Подменяет LLMClient и DuckDBManager на тестовые двойники."""
    monkeypatch.setattr("app.core.llm.client.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.planner.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.insight.LLMClient", FakeLLM)
    # In-memory DuckDB с тестовыми данными
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
                ('Одежда', 75000, '2024-02-01'),
                ('Продукты', 200000, '2024-01-01'),
                ('Продукты', 180000, '2024-02-01')
        ) AS t(category, revenue, date)
        """
    )
    monkeypatch.setattr("app.core.data.duckdb_manager.DuckDBManager", lambda path=None: db)
    monkeypatch.setattr("app.core.agent.step_executor.DuckDBManager", lambda path=None: db)
    return FakeLLM()


def test_scenario_simple_question(fake_llm: FakeLLM) -> None:
    """Сценарий 1-2: простой вопрос к данным."""
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": "e2e-session-1", "question": "Какие значения встречаются чаще всего?"},
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
        json={"session_id": "e2e-session-2", "question": "Среднее значение по категории"},
    )
    assert response.status_code == 200
    text = response.text
    assert '"type":"done"' in text


def test_scenario_dashboard_request(fake_llm: FakeLLM) -> None:
    """Сценарий 5: запрос дашборда."""
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": "e2e-session-3", "question": "Создай дашборд с последними данными"},
    )
    assert response.status_code == 200
    text = response.text
    assert '"type":"done"' in text


def test_scenario_dialogue_context(fake_llm: FakeLLM) -> None:
    """Сценарий 6: диалог с контекстом (два вопроса подряд)."""
    client = TestClient(app)
    session_id = "e2e-session-4"
    r1 = client.post("/api/chat", json={"session_id": session_id, "question": "Первый вопрос"})
    assert r1.status_code == 200
    r2 = client.post("/api/chat", json={"session_id": session_id, "question": "Второй вопрос"})
    assert r2.status_code == 200
