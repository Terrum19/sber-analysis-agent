import asyncio
import logging
import threading

import pytest

from app.api.schemas.chat import ChatRequest
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.turn_log import TurnLog, describe_plan
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.budget import report_truncation
from app.core.logging_setup import TurnContextFilter
from app.core.memory.session_memory import SessionMemory
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep

ORCHESTRATOR = "app.core.agent.orchestrator"
TURN_LOG = "app.core.agent.turn_log"
INSIGHT_LOGGER = "tests.insight"


def one_step_plan() -> ResearchPlan:
    return ResearchPlan(
        steps=[ResearchStep(step_id=0, tool="aggregate", columns=["a"], purpose="Мок шаг")],
        final_viz="bar",
    )


class FakePlanner:
    async def plan(self, question, context):
        return one_step_plan()


class FakeExecutor:
    async def execute(self, step, input_table, **_):
        return "temp_result_v1"


class FakeInsightGen:
    async def generate(self, question, result_table, **_):
        logging.getLogger(INSIGHT_LOGGER).info("инсайт строится")
        return Insight(headline="Мок инсайт", details=["Деталь"], limits="")


@pytest.fixture
def orchestrator():
    db = DuckDBManager(path=":memory:")
    db.execute("CREATE TABLE raw AS SELECT 1 AS a UNION ALL SELECT 2 AS a")
    db.execute("CREATE TABLE temp_result_v1 AS SELECT 1 AS a UNION ALL SELECT 2 AS a")
    SessionMemory("s1").set_table("raw")
    return AgentOrchestrator(
        planner=FakePlanner(),
        executor=FakeExecutor(),
        insight_gen=FakeInsightGen(),
        db=db,
    )


@pytest.fixture
def turn_records(caplog):
    caplog.set_level(logging.INFO)
    caplog.handler.addFilter(TurnContextFilter())
    return caplog


def request(question: str = "Сумма по a") -> ChatRequest:
    return ChatRequest(session_id="s1", question=question)


async def drain(orchestrator: AgentOrchestrator, question: str = "Сумма по a") -> list[dict]:
    return [event async for event in orchestrator.run(request(question))]


async def drain_task_per_event(orchestrator: AgentOrchestrator) -> list[dict]:
    events = []
    stream = orchestrator.run(request())
    while True:
        try:
            events.append(await asyncio.ensure_future(anext(stream)))
        except StopAsyncIteration:
            return events


def summary(caplog) -> str:
    lines = [
        entry.getMessage()
        for entry in caplog.records
        if entry.name == TURN_LOG and entry.getMessage().startswith("итог хода")
    ]
    assert len(lines) == 1
    return lines[0]


def messages(caplog, name: str) -> list[str]:
    return [entry.getMessage() for entry in caplog.records if entry.name == name]


def assert_turn_marked(caplog, turn_id: str) -> None:
    marked = [
        entry for entry in caplog.records if entry.name in (ORCHESTRATOR, TURN_LOG, INSIGHT_LOGGER)
    ]
    assert {entry.name for entry in marked} == {ORCHESTRATOR, TURN_LOG, INSIGHT_LOGGER}
    assert {(entry.session_id, entry.turn_id) for entry in marked} == {("s1", turn_id)}


async def test_ok_turn_logs_route_plan_steps_and_summary(orchestrator, turn_records) -> None:
    events = await drain(orchestrator)
    assert events[-1]["type"] == "done"
    orchestrator_lines = messages(turn_records, ORCHESTRATOR)
    assert "ход начат: вопрос='Сумма по a'" in orchestrator_lines
    assert "маршрут: план" in orchestrator_lines
    assert any(line.startswith("план за ") for line in orchestrator_lines)
    assert any("шагов=1 [0 aggregate] final_viz=bar" in line for line in orchestrator_lines)
    assert any(line.startswith("шаг 0 готов за ") for line in orchestrator_lines)
    timed = messages(turn_records, TURN_LOG)
    assert any(line.startswith("инсайт за ") for line in timed)
    assert any(line.startswith("график table за ") for line in timed)
    assert summary(turn_records).startswith("итог хода: ok за ")


async def test_turn_context_reaches_every_line(orchestrator, turn_records) -> None:
    events = await drain_task_per_event(orchestrator)
    assert_turn_marked(turn_records, events[-1]["data"]["turn_id"])


def test_turn_context_in_own_thread_and_loop(orchestrator, turn_records) -> None:
    result: list[list[dict]] = []
    worker = threading.Thread(target=lambda: result.append(asyncio.run(drain(orchestrator))))
    worker.start()
    worker.join()
    assert_turn_marked(turn_records, result[0][-1]["data"]["turn_id"])


async def test_failed_turn_names_stage_and_reason(orchestrator, turn_records) -> None:
    class FailingPlanner:
        async def plan(self, question, context):
            raise ConnectionError("LLM недоступна")

    orchestrator.planner = FailingPlanner()
    await drain(orchestrator)
    line = summary(turn_records)
    assert line.startswith("итог хода: failed за ")
    assert line.endswith(", этап planning: LLM недоступна")


async def test_failed_turn_without_table(orchestrator, turn_records) -> None:
    orchestrator.db.execute("DROP TABLE raw")
    stream = orchestrator.run(ChatRequest(session_id="no-table", question="Сумма"))
    events = [event async for event in stream]
    assert [event["type"] for event in events] == ["error", "done"]
    assert summary(turn_records).endswith(", этап start: Сначала подключите данные")


async def test_clarification_turn_names_failed_step(orchestrator, turn_records) -> None:
    class FailingExecutor:
        async def execute(self, step, input_table, **_):
            raise RuntimeError("колонки нет\nвозможно, имелась в виду a")

    orchestrator.executor = FailingExecutor()
    events = await drain(orchestrator)
    assert events[-1]["type"] == "clarification_needed"
    line = summary(turn_records)
    assert line.startswith("итог хода: clarification за ")
    assert line.endswith(", этап step 0: колонки нет возможно, имелась в виду a")
    assert (
        "шаг 0 упал (выполнений 1): RuntimeError('колонки нет\\nвозможно, имелась в виду a')"
        in messages(turn_records, ORCHESTRATOR)
    )


async def test_disconnected_client_ends_the_turn(orchestrator, turn_records) -> None:
    stream = orchestrator.run(request())
    first = await anext(stream)
    assert first["data"]["stage"] == "planning"
    await stream.aclose()
    line = summary(turn_records)
    assert line.startswith("итог хода: disconnected за ")
    assert line.endswith(", этап planning: клиент отключился")


async def test_crashed_turn_is_logged_and_raised(orchestrator, turn_records, monkeypatch) -> None:
    async def broken_turn(self, request):
        raise RuntimeError("память сессии недоступна")
        yield

    monkeypatch.setattr(AgentOrchestrator, "_turn", broken_turn)
    with pytest.raises(RuntimeError):
        await drain(orchestrator)
    line = summary(turn_records)
    assert line.startswith("итог хода: failed за ")
    assert "RuntimeError('память сессии недоступна')" in line


async def test_summary_counts_truncated_prompts(orchestrator, turn_records) -> None:
    class TruncatingPlanner(FakePlanner):
        async def plan(self, question, context):
            report_truncation(12_000, 4096, "упёрся в контекст")
            return await super().plan(question, context)

    orchestrator.planner = TruncatingPlanner()
    await drain(orchestrator)
    assert summary(turn_records).endswith(", обрезано промптов: 1")


async def test_small_talk_route(orchestrator, turn_records) -> None:
    await drain(orchestrator, "Привет")
    assert "маршрут: small talk" in messages(turn_records, ORCHESTRATOR)
    assert summary(turn_records).startswith("итог хода: ok")


def test_dashboard_failure_keeps_turn_ok(caplog) -> None:
    caplog.set_level(logging.INFO, logger=TURN_LOG)
    turn = TurnLog("s1")
    turn.observe({"type": "progress", "data": {"stage": "dashboard", "status": "failed"}})
    turn.observe({"type": "chart", "data": {"spec": {}}})
    turn.observe({"type": "done", "data": {"turn_id": "t1"}})
    turn.close()
    assert [entry.getMessage()[:15] for entry in caplog.records] == ["итог хода: ok з"]


def test_describe_plan_lists_steps_with_tools() -> None:
    plan = one_step_plan()
    plan.steps.append(ResearchStep(step_id=1, tool="sort", purpose="Сортировка"))
    assert describe_plan(plan) == "шагов=2 [0 aggregate, 1 sort] final_viz=bar dashboard=False"
