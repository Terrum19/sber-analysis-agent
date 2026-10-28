"""Integration-тест: полный агентный цикл.

Проверяет взаимодействие Orchestrator + Planner + Executor + Insight + SessionMemory
с in-memory DuckDB и FakeLLM.
"""

import asyncio
from typing import Any

import pytest

from app.api.schemas.chat import ChatRequest
from app.core.agent.insight import InsightGenerator
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import StepPlanner
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.core.memory.session_memory import SessionMemory


@pytest.fixture
def db():
    manager = DuckDBManager(path=":memory:")
    manager.execute(
        """
        CREATE TABLE raw AS
        SELECT *
        FROM (
            VALUES
                ('Электроника', 100000, '2024-01-01'),
                ('Электроника', 150000, '2024-02-01'),
                ('Одежда', 50000, '2024-01-01'),
                ('Одежда', 75000, '2024-02-01'),
                ('Одежда', 0, '2024-03-01')
        ) AS t(category, revenue, date)
        """
    )
    return manager


def test_full_agent_cycle(db):
    """Полный цикл: вопрос -> план -> шаги -> инсайт -> визуализация."""
    SessionMemory("cycle-test").set_table("raw")
    orch = AgentOrchestrator(
        planner=StepPlanner(llm=FakeLLM()),
        executor=StepExecutor(db=db),
        insight_gen=InsightGenerator(llm=FakeLLM(), db=db),
        db=db,
    )

    events = []

    async def _collect():
        async for event in orch.run(
            ChatRequest(session_id="cycle-test", question="Сколько категорий?")
        ):
            events.append(event)

    asyncio.run(_collect())

    types = [e["type"] for e in events]
    assert "text" in types
    assert "chart" in types
    assert "done" in types


def test_agent_cycle_with_dashboard_request(db):
    """Цикл с запросом дашборда."""
    from app.models.research_step import ResearchPlan, ResearchStep

    class DashboardPlanner:
        async def plan(self, question, context):
            return ResearchPlan(
                steps=[
                    ResearchStep(
                        step_id=0,
                        tool="aggregate",
                        columns=["category"],
                        purpose="Агрегация",
                    )
                ],
                final_viz="bar",
                dashboard_requested=True,
            )

    SessionMemory("dashboard-test").set_table("raw")
    orch = AgentOrchestrator(
        planner=DashboardPlanner(),
        executor=StepExecutor(db=db),
        insight_gen=InsightGenerator(llm=FakeLLM(), db=db),
        db=db,
    )

    events = []

    async def _collect():
        async for event in orch.run(ChatRequest(session_id="dashboard-test", question="Дашборд")):
            events.append(event)

    asyncio.run(_collect())

    types = [e["type"] for e in events]
    assert "dashboard" in types
    assert "done" in types


def test_agent_cycle_with_run_python(db, monkeypatch, codegen_available):
    """План с run_python: LLM пишет код, песочница отдаёт result.csv -> инсайт."""
    from app.core.sandbox.wasm_runtime import WASMSandbox
    from app.models.research_step import ResearchPlan, ResearchStep

    purpose = "Медиана revenue по скользящему окну 2 месяца с весами по категориям"

    class CodegenPlanner:
        async def plan(self, question, context):
            return ResearchPlan(
                steps=[ResearchStep(step_id=0, tool="run_python", arguments={"purpose": purpose})],
                final_viz="table",
            )

    class CodeLLM:
        def __init__(self):
            self.prompts = []

        async def complete(self, system, user, **kwargs):
            self.prompts.append(user)
            return "```python\nresult = df\n```"

    def fake_run(self, code, workdir):
        assert (workdir / "input.csv").read_text().startswith("category,revenue,date")
        (workdir / "result.csv").write_text("category,weighted_median\nЭлектроника,125000\n")
        return {"ok": True, "stdout": ""}

    monkeypatch.setattr(WASMSandbox, "run", fake_run)
    SessionMemory("codegen-test").set_table("raw")
    llm = CodeLLM()
    orch = AgentOrchestrator(
        planner=CodegenPlanner(),
        executor=StepExecutor(db=db, llm=llm),
        insight_gen=InsightGenerator(llm=FakeLLM(), db=db),
        db=db,
    )

    events = []

    async def _collect():
        async for event in orch.run(ChatRequest(session_id="codegen-test", question="Медиана")):
            events.append(event)

    asyncio.run(_collect())

    types = [e["type"] for e in events]
    assert "clarification_needed" not in types
    assert "text" in types
    assert "done" in types
    assert purpose in llm.prompts[0]
    rows = db.query("SELECT * FROM temp_result_v1")
    assert rows == [{"category": "Электроника", "weighted_median": 125000}]


class DashboardLLM(FakeLLM):
    def __init__(self, plan) -> None:
        super().__init__()
        self.plan = plan
        self.prompts: list[str] = []

    async def complete_structured(self, system, user, schema, **kwargs: Any):
        if schema.__name__ == "ResearchPlan":
            self.prompts.append(user)
            return self.plan.model_copy(deep=True)
        return await super().complete_structured(system, user, schema, **kwargs)

    async def complete(self, system, user, **kwargs):
        self.prompts.append(user)
        return "Электроника приносит больше всего выручки."


def revenue_step(step_id, by, purpose):
    from app.models.research_step import ResearchStep

    return ResearchStep(
        step_id=step_id,
        tool="group_by",
        input="$source",
        arguments={"by": by, "column": "revenue", "function": "SUM"},
        purpose=purpose,
    )


def test_agent_cycle_builds_dashboard_from_plan_tiles(db):
    from fastapi.testclient import TestClient

    from app.core.dashboards.store import DashboardStore
    from app.main import app
    from app.models.research_step import DashboardTilePlan, ResearchPlan

    plan = ResearchPlan(
        steps=[
            revenue_step(0, ["category"], "Выручка по категориям"),
            revenue_step(1, [], "Общая выручка"),
            revenue_step(2, ["date"], "Выручка по месяцам"),
        ],
        final_viz="auto",
        dashboard_requested=True,
        dashboard_title="Продажи",
        dashboard_tiles=[
            DashboardTilePlan(
                title="Выручка по категориям", step_id=0, chart_type="bar", insight_needed=True
            ),
            DashboardTilePlan(title="Выручка", step_id=1, chart_type="kpi", is_kpi=True),
            DashboardTilePlan(title="Выручка по месяцам", step_id=2, chart_type="line"),
        ],
    )
    llm = DashboardLLM(plan)
    SessionMemory("dashboard-tiles").set_table("raw")
    orch = AgentOrchestrator(
        planner=StepPlanner(llm=llm),
        executor=StepExecutor(db=db),
        insight_gen=InsightGenerator(llm=llm, db=db),
        db=db,
    )

    async def _collect():
        return [
            event
            async for event in orch.run(
                ChatRequest(session_id="dashboard-tiles", question="Сделай дашборд по продажам")
            )
        ]

    events = asyncio.run(_collect())

    types = [event["type"] for event in events]
    assert "от 3 до 6 плиток" in llm.prompts[0]
    assert types.index("text") < types.index("chart") < types.index("dashboard")
    assert types[-1] == "done"
    sent = events[types.index("dashboard")]["data"]
    assert sent["title"] == "Продажи"
    assert all("snapshot_data" not in tile for tile in sent["tiles"])

    saved = DashboardStore().get(sent["dashboard_id"])
    assert len(saved.tiles) == 3
    assert all(tile.result_table and tile.snapshot_data for tile in saved.tiles)
    by_title = {tile.title: tile for tile in saved.tiles}
    kpi = by_title["Выручка"]
    assert kpi.is_kpi
    assert kpi.spec["usermeta"]["chart_type"] == "kpi"
    assert kpi.snapshot_data == [{"sum_revenue": 375000}]
    assert kpi.layout == {"x": 0, "y": 0, "w": 3, "h": 2}
    assert by_title["Выручка по категориям"].layout == {"x": 0, "y": 2, "w": 6, "h": 4}
    assert by_title["Выручка по месяцам"].layout == {"x": 6, "y": 2, "w": 6, "h": 4}
    assert [tile.source_step_id for tile in saved.tiles] == [1, 0, 2]
    assert by_title["Выручка по категориям"].insight == "Электроника приносит больше всего выручки."
    assert by_title["Выручка"].insight is None
    assert by_title["Выручка по месяцам"].spec["usermeta"]["chart_type"] == "line"
    assert sum("Плитка дашборда" in prompt for prompt in llm.prompts) == 1

    response = TestClient(app).get(f"/api/dashboards/{sent['dashboard_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Продажи"
    assert [tile["title"] for tile in body["tiles"]] == [
        "Выручка",
        "Выручка по категориям",
        "Выручка по месяцам",
    ]
    assert all(tile["snapshot_data"] for tile in body["tiles"])
