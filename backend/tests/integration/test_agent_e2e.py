"""Integration-тест: полный агентный цикл с FakeLLM.

Тестирует путь: вопрос -> планировщик (FakeLLM) -> исполнение шагов ->
инсайт -> визуализация -> SSE-ответ. Не требует локальной модели Qwen.
"""

import pytest

from app.core.agent.insight import InsightGenerator
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import StepPlanner
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.core.memory.session_memory import SessionMemory


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """Подменяет LLMClient на FakeLLM в planner и insight_gen."""
    monkeypatch.setattr("app.core.llm.client.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.planner.LLMClient", FakeLLM)
    monkeypatch.setattr("app.core.agent.insight.LLMClient", FakeLLM)
    return FakeLLM()


@pytest.fixture
def test_db() -> DuckDBManager:
    """Создаёт in-memory DuckDB с тестовой таблицей raw."""
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
    return db


@pytest.fixture
def test_orchestrator(test_db: DuckDBManager, monkeypatch: pytest.MonkeyPatch) -> AgentOrchestrator:
    """Оркестратор с тестовой БД и FakeLLM."""
    # Подменяем DuckDBManager так, чтобы InsightGenerator использовал test_db
    monkeypatch.setattr("app.core.data.duckdb_manager.DuckDBManager", lambda path=None: test_db)
    return AgentOrchestrator(
        planner=StepPlanner(llm=FakeLLM()),
        executor=StepExecutor(db=test_db),
        insight_gen=InsightGenerator(llm=FakeLLM()),
        db=test_db,
    )


async def test_full_research_flow(fake_llm: FakeLLM, test_orchestrator: AgentOrchestrator) -> None:
    """Полный цикл исследования с FakeLLM."""
    from app.api.schemas.chat import ChatRequest

    SessionMemory("test-session-1").set_table("raw")
    events = []
    async for event in test_orchestrator.run(
        ChatRequest(session_id="test-session-1", question="Сколько категорий?")
    ):
        events.append(event)

    assert len(events) > 0
    types = [e["type"] for e in events]
    assert "text" in types
    assert "chart" in types
    assert "done" in types
