"""Unit-тесты AgentOrchestrator с моками компонентов."""

import pytest

from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import StepPlanner
from app.core.agent.insight import InsightGenerator
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.api.schemas.chat import ChatRequest


class FakeExecutor:
    """МOCK исполнителя: возвращает фиксированную таблицу."""
    async def execute(self, step, input_table):
        return "temp_result_v1"


class FakeInsightGen:
    """МOCK генератора инсайта."""
    async def generate(self, question, result_table):
        from app.models.insight import Insight
        return Insight(
            headline="Мок инсайт",
            details=["Деталь 1"],
            limits="",
        )


class FakePlanner:
    """МOCK планировщика."""
    async def plan(self, question, context):
        from app.models.research_step import ResearchPlan, ResearchStep
        return ResearchPlan(
            steps=[
                ResearchStep(
                    step_id=0,
                    tool="aggregate",
                    columns=["category"],
                    needs_llm_codegen=False,
                    purpose="Мок шаг",
                )
            ],
            final_viz="bar",
            dashboard_requested=False,
        )


@pytest.fixture
def orchestrator():
    db = DuckDBManager(path=":memory:")
    db.execute("CREATE TABLE raw AS SELECT 1 AS a UNION ALL SELECT 2 AS a")
    db.execute("CREATE TABLE temp_result_v1 AS SELECT 1 AS a UNION ALL SELECT 2 AS a")
    return AgentOrchestrator(
        planner=FakePlanner(),
        executor=FakeExecutor(),
        insight_gen=FakeInsightGen(),
        db=db,
    )


def test_run_returns_events(orchestrator):
    import asyncio
    events = []
    async def _collect():
        async for event in orchestrator.run(
            ChatRequest(session_id="s1", question="Тест")
        ):
            events.append(event)
    asyncio.run(_collect())
    types = [e["type"] for e in events]
    assert "text" in types
    assert "chart" in types
    assert "done" in types


def test_run_error_in_step(orchestrator):
    import asyncio

    class FailingExecutor:
        async def execute(self, step, input_table):
            raise ValueError("Таблица не найдена")

    orch = AgentOrchestrator(
        planner=FakePlanner(),
        executor=FailingExecutor(),
        insight_gen=FakeInsightGen(),
        db=orchestrator.db,
    )
    events = []
    async def _collect():
        async for event in orch.run(
            ChatRequest(session_id="s2", question="Тест ошибки")
        ):
            events.append(event)
    asyncio.run(_collect())
    types = [e["type"] for e in events]
    assert "clarification_needed" in types
    assert "done" not in types
    # Проверяем содержимое события уточнения
    clar_events = [e for e in events if e["type"] == "clarification_needed"]
    assert len(clar_events) == 1
    assert clar_events[0]["data"]["step_id"] == 0
    assert "Таблица не найдена" in clar_events[0]["data"]["error"]
    assert "suggestions" in clar_events[0]["data"]
