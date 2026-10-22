"""Бенчмарк: производительность агентного цикла."""

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


def test_orchestrator_cycle_time(db):
    SessionMemory("bench").set_table("raw", db.columns("raw"))
    orch = AgentOrchestrator(
        planner=StepPlanner(llm=FakeLLM()),
        executor=StepExecutor(db=db),
        insight_gen=InsightGenerator(llm=FakeLLM(), db=db),
        db=db,
    )

    events = []

    async def _collect():
        async for event in orch.run(ChatRequest(session_id="bench", question="Сколько записей?")):
            events.append(event)

    asyncio.run(_collect())
    assert len(events) > 0
    types = [e["type"] for e in events]
    assert "text" in types
    assert "chart" in types
    assert "done" in types
