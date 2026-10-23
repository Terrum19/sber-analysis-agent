"""Integration-тест: SessionMemory + ProfileMemory между запросами и таблицами."""

import asyncio

import pytest

from app.api.schemas.chat import ChatRequest
from app.core.agent.insight import InsightGenerator
from app.core.agent.orchestrator import NO_TABLE_MESSAGE, AgentOrchestrator
from app.core.agent.planner import StepPlanner
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.core.memory.session_memory import SessionMemory
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep


class SpyPlanner:
    """Запоминает колонки из контекста каждого вызова."""

    def __init__(self) -> None:
        self.columns: list[list[str]] = []

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.columns.append(context["columns"])
        return ResearchPlan(steps=[ResearchStep(step_id=1, tool="copy")], final_viz="bar")


class CopyExecutor:
    """Копирует входную таблицу в temp_result_vN, не завися от её колонок."""

    def __init__(self, db: DuckDBManager) -> None:
        self.db = db

    async def execute(self, step: ResearchStep, input_table: str, **_: object) -> str:
        return self.db.save_step_result(input_table, f"SELECT * FROM {input_table}")


class StubInsight:
    async def generate(self, question: str, result_table: str, **_: object) -> Insight:
        return Insight(headline=f"ответ по {result_table}", details=[])


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE sales AS SELECT * FROM (VALUES ('msk', 10.0), ('spb', 4.0)) "
        "AS rows(region, revenue)"
    )
    manager.execute(
        "CREATE TABLE users AS SELECT * FROM (VALUES (25, 'msk'), (40, 'spb')) AS rows(age, city)"
    )
    yield manager
    manager.close()


@pytest.fixture
def agent(db):
    return AgentOrchestrator(
        planner=SpyPlanner(), executor=CopyExecutor(db), insight_gen=StubInsight(), db=db
    )


def ask(agent: AgentOrchestrator, session: str, question: str) -> list[dict]:
    async def collect() -> list[dict]:
        request = ChatRequest(session_id=session, question=question)
        return [event async for event in agent.run(request)]

    return asyncio.run(collect())


def test_memory_persists_between_requests(db):
    """Состояние сессии сохраняется между двумя запросами."""
    db.execute("CREATE TABLE raw AS SELECT * FROM (VALUES ('a', 1), ('b', 2)) AS t(category, n)")
    SessionMemory("mem-session").set_table("raw", db.columns("raw"))
    orch = AgentOrchestrator(
        planner=StepPlanner(llm=FakeLLM()),
        executor=StepExecutor(db=db),
        insight_gen=InsightGenerator(llm=FakeLLM(), db=db),
        db=db,
    )

    events1 = ask(orch, "mem-session", "Первый вопрос")
    events2 = ask(orch, "mem-session", "Второй вопрос")

    assert events1[-1]["type"] == "done"
    assert events2[-1]["type"] == "done"
    assert len(SessionMemory("mem-session").build_context("")["messages"]) == 2


def test_planner_gets_columns_of_session_table(agent, db):
    SessionMemory("sales-session").set_table("sales", db.columns("sales"))
    SessionMemory("users-session").set_table("users", db.columns("users"))

    ask(agent, "sales-session", "Выручка по регионам")
    ask(agent, "users-session", "Возраст по городам")

    assert agent.planner.columns == [["region", "revenue"], ["age", "city"]]


def test_session_without_table_asks_to_connect_data(agent):
    events = ask(agent, "no-table", "Выручка по регионам")

    assert events == [
        {"type": "error", "data": {"message": NO_TABLE_MESSAGE}},
        {"type": "done", "data": {}},
    ]
    assert agent.planner.columns == []


def test_chart_view_change_uses_chart_of_current_table(agent, db):
    SessionMemory("switch").set_table("sales", db.columns("sales"))
    ask(agent, "switch", "Выручка по регионам")

    SessionMemory("switch").set_table("users", db.columns("users"))
    events = ask(agent, "switch", "Сделай таблицей")
    assert len(agent.planner.columns) == 2
    assert agent.planner.columns[-1] == ["age", "city"]
    assert next(event["type"] for event in events if event["type"] != "progress") == "text"

    SessionMemory("switch").set_table("sales", db.columns("sales"))
    events = ask(agent, "switch", "Сделай таблицей")
    assert len(agent.planner.columns) == 2
    assert [event["type"] for event in events] == ["chart", "done"]
    assert events[0]["data"]["caption"] == "ответ по temp_result_v1"
