"""Integration-тест: полный агентный цикл.

Проверяет взаимодействие Orchestrator + Planner + Executor + Insight + SessionMemory
с in-memory DuckDB и FakeLLM.
"""

import asyncio

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
                ('Одежда', 75000, '2024-02-01')
        ) AS t(category, revenue, date)
        """
    )
    return manager


def test_full_agent_cycle(db):
    """Полный цикл: вопрос -> план -> шаги -> инсайт -> визуализация."""
    SessionMemory("cycle-test").set_table("raw", db.columns("raw"))
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

    SessionMemory("dashboard-test").set_table("raw", db.columns("raw"))
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
    SessionMemory("codegen-test").set_table("raw", db.columns("raw"))
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
