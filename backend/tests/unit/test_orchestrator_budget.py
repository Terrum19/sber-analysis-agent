"""Ход всегда закрывается в бюджет, даже если LLM «висит»."""

import asyncio
import time

from app.api.schemas.chat import ChatRequest
from app.config import settings
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory


class HangingPlanner:
    def __init__(self) -> None:
        self.cancelled = False

    async def plan(self, question: str, context: dict):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def test_turn_deadline_returns_error_and_done(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_turn_timeout_s", 2)
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE raw AS SELECT 1 AS a")
    SessionMemory("budget").set_table("raw")
    planner = HangingPlanner()
    agent = AgentOrchestrator(
        planner=planner,
        executor=object(),
        insight_gen=object(),
        db=db,
    )
    started = time.perf_counter()
    events = [
        event
        async for event in agent.run(ChatRequest(session_id="budget", question="Сложный вопрос"))
    ]
    elapsed = time.perf_counter() - started
    assert elapsed < 3
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["message"] == "timeout"
    assert any(event["type"] == "error" for event in events)
    # Задача планировщика отменена, а не осталась висеть.
    await asyncio.sleep(0)
    assert planner.cancelled
    db.close()


async def test_simple_question_skips_the_planner() -> None:
    from app.core.agent.insight import InsightGenerator
    from app.core.data.profiling import ColumnStats, TableProfile
    from app.core.memory.profile_memory import ProfileMemory

    class NoPlanner:
        async def plan(self, question, context):  # pragma: no cover
            raise AssertionError("простой вопрос не должен идти в planner")

    class Executor:
        async def execute(self, step, input_table, **_):
            return "temp_result_v1"

    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE raw AS SELECT 'msk' AS region, 10.0 AS amount")
    db.execute("CREATE TABLE temp_result_v1 AS SELECT 2 AS n")
    ProfileMemory(db).save_profile(
        TableProfile(
            table="raw",
            row_count=2,
            column_count=2,
            profiled_at="2026-10-28T00:00:00+00:00",
            columns=[
                ColumnStats("region", "VARCHAR", "text"),
                ColumnStats("amount", "DOUBLE", "numeric"),
            ],
        )
    )
    SessionMemory("simple").set_table("raw")
    agent = AgentOrchestrator(
        planner=NoPlanner(),
        executor=Executor(),
        insight_gen=InsightGenerator(llm=object(), db=db),
        db=db,
    )
    events = [
        event
        async for event in agent.run(ChatRequest(session_id="simple", question="Число строк?"))
    ]
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["route"] == "simple_count"
    assert any(event["type"] == "text" and "2" in event["data"]["headline"] for event in events if event["type"] == "text")
    db.close()


async def test_done_always_contains_turn_metrics() -> None:
    from app.core.agent.metrics import TurnMetrics, reset_metrics, set_metrics
    from app.core.agent.orchestrator import _done

    metrics = TurnMetrics(session_id="s", turn_id="t", question="q", route="simple_count")
    metrics.record_llm(12, 3, 40)
    token = set_metrics(metrics)
    try:
        done = _done("t")
    finally:
        reset_metrics(token)
    assert done["type"] == "done"
    assert done["data"]["turn_id"] == "t"
    assert done["data"]["route"] == "simple_count"
    assert done["data"]["llm_calls"] == 1
    assert done["data"]["prompt_tokens"] == 12
    assert done["data"]["total_ms"] >= 1
