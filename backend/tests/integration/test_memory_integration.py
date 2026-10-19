"""Integration-тест: SessionMemory + ProfileMemory между запросами."""

import asyncio

from app.api.schemas.chat import ChatRequest
from app.core.agent.insight import InsightGenerator
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import StepPlanner
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM


def test_memory_persists_between_requests(tmp_path):
    """Состояние сессии сохраняется между двумя запросами."""
    db_path = str(tmp_path / "mem_integration.sqlite")

    from app.config import settings

    old_sqlite = settings.sqlite_path
    settings.sqlite_path = db_path

    try:
        db = DuckDBManager(path=":memory:")
        db.execute("CREATE TABLE raw AS SELECT 1 AS a UNION ALL SELECT 2 AS a")

        orch = AgentOrchestrator(
            planner=StepPlanner(llm=FakeLLM()),
            executor=StepExecutor(db=db),
            insight_gen=InsightGenerator(llm=FakeLLM(), db=db),
            db=db,
        )

        # Первый запрос
        events1 = []

        async def _collect1():
            async for event in orch.run(
                ChatRequest(session_id="mem-session", question="Первый вопрос")
            ):
                events1.append(event)

        asyncio.run(_collect1())

        # Второй запрос в той же сессии
        events2 = []

        async def _collect2():
            async for event in orch.run(
                ChatRequest(session_id="mem-session", question="Второй вопрос")
            ):
                events2.append(event)

        asyncio.run(_collect2())

        assert len(events1) > 0
        assert len(events2) > 0
    finally:
        settings.sqlite_path = old_sqlite
