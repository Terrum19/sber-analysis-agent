"""Расширенные E2E-сценарии с FakeLLM."""

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


def test_empty_question(fake_llm):
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": "ext-1", "question": ""},
    )
    assert response.status_code == 200


def test_viz_type_change(fake_llm):
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": "ext-2", "question": "Покажи линейный график"},
    )
    assert response.status_code == 200
    text = response.text
    assert '"type":"done"' in text


def test_large_question(fake_llm):
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={
            "session_id": "ext-3",
            "question": "Анализируй все категории, посчитай сумму, среднее, максимум и минимум по каждой, сделай сравнительный отчёт",
        },
    )
    assert response.status_code == 200
