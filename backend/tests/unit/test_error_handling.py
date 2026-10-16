"""Тесты обработки ошибок в компонентах агента."""

import asyncio
import pytest

from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.models.research_step import ResearchStep


@pytest.fixture
def db():
    return DuckDBManager(path=":memory:")


def test_step_executor_missing_table(db):
    executor = StepExecutor(db=db)
    step = ResearchStep(
        step_id=0,
        tool="aggregate",
        columns=["x"],
        needs_llm_codegen=False,
        purpose="test",
    )
    with pytest.raises(ValueError):
        asyncio.run(executor.execute(step, "absent_table"))


def test_step_executor_unknown_tool(db):
    db.execute("CREATE TABLE t AS SELECT 1 AS x")
    executor = StepExecutor(db=db)
    step = ResearchStep(
        step_id=0,
        tool="nonexistent",
        columns=["x"],
        needs_llm_codegen=False,
        purpose="test",
    )
    with pytest.raises(ValueError):
        asyncio.run(executor.execute(step, "t"))
