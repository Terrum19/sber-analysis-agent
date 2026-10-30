"""«Продолжить» остановленный или упавший ход с чекпоинта: шаги не повторяются."""

import asyncio

import pytest

from app.api.schemas.chat import ChatRequest
from app.config import settings
from app.core.agent.orchestrator import (
    NOTHING_TO_RESUME,
    RUNNING_TURNS,
    STILL_RUNNING,
    AgentOrchestrator,
)
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.result_retention import collect_keep
from app.core.memory.session_memory import SessionMemory
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep

STEP_SQL = {
    1: "SELECT region, SUM(amount) AS revenue FROM raw GROUP BY region ORDER BY region",
    2: "SELECT region, SUM(amount) AS revenue FROM raw WHERE amount > 3 GROUP BY region",
}


class TwoStepPlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.calls += 1
        return ResearchPlan(
            steps=[
                ResearchStep(step_id=1, tool="group", purpose="по регионам"),
                ResearchStep(step_id=2, tool="group", purpose="без мелких заказов"),
            ],
            final_viz="bar",
        )


class StoppingExecutor:
    """Выполняет шаги SQL из STEP_SQL; на шаге stop_at ход останавливается."""

    def __init__(self, db: DuckDBManager, stop_at: int | None = None) -> None:
        self.db = db
        self.stop_at = stop_at
        self.executed: list[int] = []

    async def execute(self, step: ResearchStep, input_table: str, **_: object) -> str:
        if step.step_id == self.stop_at:
            # Так ход видит кнопку «Стоп»: клиент ушёл, задача хода отменена.
            raise asyncio.CancelledError
        self.executed.append(step.step_id)
        return self.db.save_step_result("raw", STEP_SQL[step.step_id])


class FakeInsight:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def generate(self, *_: object, **__: object) -> Insight:
        self.calls += 1
        if self.fail:
            raise RuntimeError("LLM недоступна")
        return Insight(headline="Москва лидирует", details=["msk > spb"])


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "sessions" / "sessions.sqlite"))


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES ('msk', 10.0), ('spb', 4.0), ('msk', 2.5)) "
        "AS rows(region, amount)"
    )
    SessionMemory("s1").set_table("raw")
    yield manager
    manager.close()


def agent(db, executor=None, insight=None, planner=None) -> AgentOrchestrator:
    return AgentOrchestrator(
        planner=planner or TwoStepPlanner(),
        executor=executor or StoppingExecutor(db),
        insight_gen=insight or FakeInsight(),
        db=db,
    )


async def collect(orchestrator: AgentOrchestrator, **request: object) -> list[dict]:
    chat = ChatRequest(session_id="s1", **request)
    return [event async for event in orchestrator.run(chat)]


def turn_id_of(events: list[dict]) -> str:
    return next(event["data"]["turn_id"] for event in events if event["type"] == "progress")


def last_turn() -> dict:
    return SessionMemory("s1")._state["turns"][-1]


async def stopped_turn(db) -> str:
    """Ход, остановленный на втором шаге; его turn_id из первого события."""
    events: list[dict] = []
    with pytest.raises(asyncio.CancelledError):
        async for event in agent(db, StoppingExecutor(db, stop_at=2)).run(
            ChatRequest(session_id="s1", question="выручка по регионам")
        ):
            events.append(event)
    return turn_id_of(events)


async def test_stopped_turn_continues_from_the_first_unfinished_step(db) -> None:
    turn_id = await stopped_turn(db)
    assert last_turn()["status"] == "failed"
    assert last_turn()["checkpoint"]["step_tables"].keys() == {"1"}

    planner, executor = TwoStepPlanner(), StoppingExecutor(db)
    events = await collect(agent(db, executor, planner=planner), question="", resume_turn=turn_id)

    assert planner.calls == 0
    assert executor.executed == [2]
    assert [event["type"] for event in events if event["type"] != "progress"] == [
        "text",
        "chart",
        "done",
    ]
    steps = [
        (event["data"]["step_id"], event["data"]["status"])
        for event in events
        if event["type"] == "progress" and event["data"]["stage"] == "step"
    ]
    assert steps == [(1, "finished"), (2, "started"), (2, "finished")]
    assert events[-1]["data"]["turn_id"] == turn_id
    turn = last_turn()
    assert turn["status"] == "ok"
    assert turn["question"] == "выручка по регионам"
    assert "checkpoint" not in turn
    assert len(SessionMemory("s1")._state["turns"]) == 1


async def test_turn_failed_after_the_steps_continues_with_the_answer_only(db) -> None:
    events = await collect(agent(db, insight=FakeInsight(fail=True)), question="выручка")
    assert [event["type"] for event in events][-2:] == ["error", "done"]

    executor, insight = StoppingExecutor(db), FakeInsight()
    events = await collect(
        agent(db, executor, insight), question="", resume_turn=turn_id_of(events)
    )
    assert executor.executed == []
    assert insight.calls == 1
    assert [event["type"] for event in events if event["type"] != "progress"] == [
        "text",
        "chart",
        "done",
    ]


async def test_unknown_turn_has_nothing_to_resume(db) -> None:
    events = await collect(agent(db), question="", resume_turn="unknown")
    assert events == [
        {"type": "error", "data": {"message": NOTHING_TO_RESUME}},
        {"type": "done", "data": {}},
    ]


async def test_answered_turn_replays_its_answer_without_llm(db) -> None:
    """Страница перезагрузилась посреди хода, а ход ответил: ответ отдаётся из памяти."""
    first = await collect(agent(db), question="выручка")
    turn_id = first[-1]["data"]["turn_id"]
    chart = next(event["data"] for event in first if event["type"] == "chart")

    planner, executor, insight = TwoStepPlanner(), StoppingExecutor(db), FakeInsight()
    events = await collect(agent(db, executor, insight, planner), question="", resume_turn=turn_id)
    assert planner.calls == insight.calls == 0
    assert executor.executed == []
    assert [event["type"] for event in events] == ["text", "chart", "done"]
    assert events[0]["data"]["headline"] == "Москва лидирует"
    assert events[1]["data"]["result_table"] == chart["result_table"]
    assert events[1]["data"]["spec"]["usermeta"]["chart_type"] == "bar"
    assert events[-1]["data"]["turn_id"] == turn_id
    assert len(SessionMemory("s1")._state["turns"]) == 1


async def test_answered_turn_without_its_chart_table_replays_the_text(db) -> None:
    first = await collect(agent(db), question="выручка")
    chart = next(event["data"] for event in first if event["type"] == "chart")
    db.execute(f"DROP TABLE {chart['result_table']}")
    events = await collect(agent(db), question="", resume_turn=first[-1]["data"]["turn_id"])
    assert [event["type"] for event in events] == ["text", "done"]


async def test_answered_turn_without_a_chart_replays_the_text(db) -> None:
    first = await collect(agent(db), question="привет")
    events = await collect(agent(db), question="", resume_turn=first[-1]["data"]["turn_id"])
    assert [event["type"] for event in events] == ["text", "done"]
    assert events[0]["data"]["headline"] == first[0]["data"]["headline"]


async def test_turn_still_running_is_not_started_twice(db) -> None:
    """Страница перезагрузилась, а ход на бэкенде ещё идёт: второй раз его не запускаем."""
    turn_id = await stopped_turn(db)
    assert not RUNNING_TURNS
    RUNNING_TURNS.add(turn_id)
    try:
        executor = StoppingExecutor(db)
        events = await collect(agent(db, executor), question="", resume_turn=turn_id)
    finally:
        RUNNING_TURNS.discard(turn_id)
    assert executor.executed == []
    assert events == [
        {"type": "error", "data": {"message": STILL_RUNNING}},
        {"type": "done", "data": {}},
    ]
    # Закончился — продолжается как обычно, и после хода в списке идущих пусто.
    events = await collect(agent(db), question="", resume_turn=turn_id)
    assert events[-1]["data"]["turn_id"] == turn_id
    assert not RUNNING_TURNS


async def test_cleanup_keeps_the_tables_of_a_stopped_turn(db) -> None:
    await stopped_turn(db)
    tables = set(last_turn()["checkpoint"]["step_tables"].values())
    assert tables and tables <= collect_keep()
