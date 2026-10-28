"""Сценарии #197 через оркестратор: факт из хода 1 на ходе 7, «запомни …» в ходе 1."""

import asyncio

import pytest

from app.api.schemas.chat import ChatRequest
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep

REVENUE = ResearchStep(
    step_id=0,
    tool="group_by",
    arguments={"by": ["region"], "column": "revenue", "function": "SUM"},
    purpose="Выручка по регионам",
)


class ScriptedPlanner:
    """План по вопросу; запоминает контекст каждого вызова."""

    def __init__(self, plans: dict[str, ResearchPlan]) -> None:
        self.plans = plans
        self.contexts: list[dict] = []

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.contexts.append(context)
        if "executed" in context:
            # Достройка после read_history: ответ уже в найденной истории.
            return ResearchPlan(steps=[], final_viz="none")
        return self.plans.get(question, ResearchPlan(steps=[REVENUE], final_viz="none"))


class TableInsight:
    """Ответ — первая строка результата: ход 7 отвечает тем, что нашёл read_history."""

    def __init__(self, db: DuckDBManager) -> None:
        self.db = db
        self.memories: list[list[str]] = []

    async def generate(self, question, result_table, memories=None, **_) -> Insight:
        self.memories.append(list(memories or []))
        row = self.db.to_records(result_table, limit=1)[0]
        return Insight(headline=str(row.get("answer", row)), details=[])


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE sales AS SELECT * FROM (VALUES ('msk', 10.0), ('spb', 4.0)) "
        "AS rows(region, revenue)"
    )
    SessionMemory("flow").set_table("sales")
    yield manager
    manager.close()


def ask(agent: AgentOrchestrator, question: str) -> list[dict]:
    async def collect() -> list[dict]:
        request = ChatRequest(session_id="flow", question=question)
        return [event async for event in agent.run(request)]

    return asyncio.run(collect())


def agent_with(db, planner) -> AgentOrchestrator:
    return AgentOrchestrator(
        planner=planner, executor=StepExecutor(db=db), insight_gen=TableInsight(db), db=db
    )


def test_turn_7_answers_fact_from_turn_1_through_history(db):
    history_step = ResearchStep(
        step_id=0, tool="read_history", arguments={"query": "выручка"}, purpose="Ход про выручку"
    )
    planner = ScriptedPlanner(
        {"Что ты ответил про выручку в начале?": ResearchPlan(steps=[history_step])}
    )
    agent = agent_with(db, planner)
    ask(agent, "Какая выручка по регионам?")
    fact = SessionMemory("flow")._state["turns"][0]["headline"]
    assert "msk" in fact
    for number in range(2, 7):
        ask(agent, f"Сколько строк, вопрос {number}?")

    events = ask(agent, "Что ты ответил про выручку в начале?")

    # Ход 1 вне окна из 5 ходов: в контексте ходы 2–6, ответ — через read_history.
    first_context = planner.contexts[-2]
    assert [t["number"] for t in first_context["turns"]] == [2, 3, 4, 5, 6]
    assert fact in planner.contexts[-1]["executed"]
    text = next(e for e in events if e["type"] == "text")
    assert text["data"]["headline"] == fact
    assert events[-1]["type"] == "done"
    # Вызов истории — шаг хода: виден в логе хода и в событиях progress.
    last = SessionMemory("flow")._state["turns"][-1]
    assert [step["tool"] for step in last["steps"]] == ["read_history"]
    started = [e["data"].get("tool") for e in events if e["type"] == "progress"]
    assert "read_history" in started


def test_remember_in_turn_1_reaches_turn_5_after_restart(db):
    note = "выручка считается без НДС"
    planner = ScriptedPlanner({"Запомни, что выручка без НДС": ResearchPlan(remember=[note])})
    agent = agent_with(db, planner)

    events = ask(agent, "Запомни, что выручка без НДС")
    # Только просьба запомнить: подтверждение без шагов, инсайта и графика.
    assert [e["type"] for e in events if e["type"] != "progress"] == ["text", "done"]
    assert events[-2]["data"]["headline"] == f"Запомнено: «{note}»"
    assert agent.insight_gen.memories == []
    first = SessionMemory("flow")._state["turns"][0]
    assert first["status"] == "ok"
    assert first["headline"] == f"Запомнено: «{note}»"

    for number in range(2, 5):
        ask(agent, f"Вопрос {number}")

    # Перезапуск backend: новый оркестратор, память — из SQLite.
    restarted_planner = ScriptedPlanner({})
    restarted = agent_with(db, restarted_planner)
    ask(restarted, "Выручка по регионам")
    assert restarted_planner.contexts[-1]["memories"] == [note]
    assert restarted.insight_gen.memories[-1] == [note]
    saved = SessionMemory("flow").memories(include_disabled=True)
    assert [(m["text"], m["source"], m["enabled"]) for m in saved] == [(note, "user", True)]


def test_remember_with_data_question_confirms_in_details(db):
    plan = ResearchPlan(steps=[REVENUE], final_viz="none", remember=["выручка без НДС"])
    agent = agent_with(db, ScriptedPlanner({"Выручка по регионам, и запомни: без НДС": plan}))
    events = ask(agent, "Выручка по регионам, и запомни: без НДС")
    text = next(e for e in events if e["type"] == "text")
    assert text["data"]["details"][-1] == "Запомнено: «выручка без НДС»"
    assert agent.insight_gen.memories[-1] == ["выручка без НДС"]
