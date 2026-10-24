"""Unit-тесты AgentOrchestrator с моками компонентов."""

import pytest

from app.api.schemas.chat import ChatRequest
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory


class FakeExecutor:
    """МOCK исполнителя: возвращает фиксированную таблицу."""

    async def execute(self, step, input_table, **_):
        return "temp_result_v1"


class FakeInsightGen:
    """МOCK генератора инсайта."""

    async def generate(self, question, result_table, **_):
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
    for session in ("s1", "s2"):
        SessionMemory(session).set_table("raw", db.columns("raw"))
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
        async for event in orchestrator.run(ChatRequest(session_id="s1", question="Тест")):
            events.append(event)

    asyncio.run(_collect())
    types = [e["type"] for e in events]
    assert "text" in types
    assert "chart" in types
    assert "done" in types


def test_run_error_in_step(orchestrator):
    import asyncio

    class FailingExecutor:
        async def execute(self, step, input_table, **_):
            raise ValueError("Таблица не найдена")

    orch = AgentOrchestrator(
        planner=FakePlanner(),
        executor=FailingExecutor(),
        insight_gen=FakeInsightGen(),
        db=orchestrator.db,
    )
    events = []

    async def _collect():
        async for event in orch.run(ChatRequest(session_id="s2", question="Тест ошибки")):
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


def collect(orchestrator, session_id: str) -> list[dict]:
    import asyncio

    async def _collect():
        request = ChatRequest(session_id=session_id, question="Тест")
        return [event async for event in orchestrator.run(request)]

    return asyncio.run(_collect())


def stages(events: list[dict]) -> list[str]:
    return [
        f"{event['data']['stage']}:{event['data']['status']}"
        if event["type"] == "progress"
        else event["type"]
        for event in events
    ]


def test_progress_events_precede_the_answer(orchestrator):
    from app.api.schemas.chat import ChatEvent, ProgressPayload

    events = collect(orchestrator, "s1")

    assert stages(events) == [
        "planning:started",
        "planning:finished",
        "step:started",
        "step:finished",
        "insight:started",
        "text",
        "chart:started",
        "chart",
        "done",
    ]
    for event in events:
        ChatEvent(**event)
        if event["type"] == "progress":
            ProgressPayload(**event["data"])
    progress = [event["data"] for event in events if event["type"] == "progress"]
    assert progress[0] == {"stage": "planning", "status": "started"}
    assert progress[1] == {"stage": "planning", "status": "finished", "total_steps": 1}
    assert progress[2] == {
        "stage": "step",
        "status": "started",
        "step_id": 0,
        "tool": "aggregate",
        "purpose": "Мок шаг",
    }
    assert progress[3]["status"] == "finished"
    assert progress[3]["step_id"] == 0
    assert progress[3]["elapsed_ms"] >= 0
    assert progress[4] == {"stage": "insight", "status": "started"}
    assert progress[5] == {"stage": "chart", "status": "started"}


def test_failed_step_reports_progress(orchestrator):
    class FailingExecutor:
        async def execute(self, step, input_table, **_):
            raise NotImplementedError("Инструмент недоступен")

    orchestrator.executor = FailingExecutor()
    events = collect(orchestrator, "s2")

    assert stages(events) == [
        "planning:started",
        "planning:finished",
        "step:started",
        "step:failed",
        "clarification_needed",
    ]
    failed = events[3]["data"]
    assert failed["step_id"] == 0
    assert failed["error"] == "Инструмент недоступен"
    assert failed["elapsed_ms"] >= 0


def test_empty_plan_reports_zero_steps(orchestrator):
    from app.models.research_step import ResearchPlan

    class EmptyPlanner:
        async def plan(self, question, context):
            return ResearchPlan(steps=[], final_viz="bar", dashboard_requested=False)

    orchestrator.planner = EmptyPlanner()
    events = collect(orchestrator, "s1")

    assert stages(events) == ["planning:started", "planning:finished", "clarification_needed"]
    assert events[1]["data"]["total_steps"] == 0


def test_planner_failure_ends_the_stream(orchestrator):
    class FailingPlanner:
        async def plan(self, question, context):
            raise RuntimeError("LLM timeout")

    orchestrator.planner = FailingPlanner()
    events = collect(orchestrator, "s1")

    assert stages(events) == ["planning:started", "planning:failed", "error", "done"]
    assert events[1]["data"]["error"] == "LLM timeout"
    assert events[2]["data"]["message"]


def test_insight_failure_ends_the_stream(orchestrator):
    class FailingInsightGen:
        async def generate(self, question, result_table, **_):
            raise RuntimeError("LLM timeout")

    orchestrator.insight_gen = FailingInsightGen()
    events = collect(orchestrator, "s1")

    assert stages(events)[-4:] == ["insight:started", "insight:failed", "error", "done"]
    assert events[-3]["data"]["error"] == "LLM timeout"


def test_dashboard_failure_keeps_the_answer(orchestrator, monkeypatch):
    from app.core.dashboards.generator import DashboardGenerator
    from app.models.research_step import ResearchPlan

    class DashboardPlanner(FakePlanner):
        async def plan(self, question, context):
            plan = await super().plan(question, context)
            return ResearchPlan(steps=plan.steps, final_viz="bar", dashboard_requested=True)

    async def failing_generate(self, *args, **kwargs):
        raise RuntimeError("SQLite недоступна")

    orchestrator.planner = DashboardPlanner()
    monkeypatch.setattr(DashboardGenerator, "generate", failing_generate)
    events = collect(orchestrator, "s1")

    assert stages(events)[-4:] == ["chart:started", "chart", "dashboard:failed", "done"]
    assert "error" not in stages(events)
    assert events[-2]["data"]["error"] == "SQLite недоступна"
