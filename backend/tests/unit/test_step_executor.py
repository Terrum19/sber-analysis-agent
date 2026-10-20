"""Unit-тесты StepExecutor: выполнение шагов, обработка ошибок."""

import asyncio

import pytest

from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.sandbox import codegen_runner
from app.models.research_step import ResearchStep


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


def test_execute_known_tool_returns_result_table(db):
    executor = StepExecutor(db=db)
    step = ResearchStep(
        step_id=0,
        tool="aggregate",
        columns=["category"],
        needs_llm_codegen=False,
        purpose="Агрегируем по категории",
    )
    result = asyncio.run(executor.execute(step, "raw"))
    assert result is not None
    assert isinstance(result, str)


def test_execute_missing_table_raises_valueerror(db):
    executor = StepExecutor(db=db)
    step = ResearchStep(
        step_id=0,
        tool="aggregate",
        columns=["category"],
        needs_llm_codegen=False,
        purpose="test",
    )
    with pytest.raises(ValueError, match="не найдена"):
        asyncio.run(executor.execute(step, "nonexistent_table"))


def test_execute_unknown_tool_raises_valueerror(db):
    executor = StepExecutor(db=db)
    step = ResearchStep(
        step_id=0,
        tool="nonexistent_tool",
        columns=["category"],
        needs_llm_codegen=False,
        purpose="test",
    )
    with pytest.raises(ValueError, match="не зарегистрирован"):
        asyncio.run(executor.execute(step, "raw"))


def test_execute_needs_llm_codegen_runs_generated_code(db, monkeypatch):
    calls = []

    async def fake_runner(step, input_table, manager, llm, sandbox):
        calls.append((step.purpose, input_table, manager is db))
        return "temp_result_v9"

    monkeypatch.setattr(codegen_runner, "run_generated_code", fake_runner)
    executor = StepExecutor(db=db)
    step = ResearchStep(
        step_id=0,
        tool=None,
        columns=["category"],
        needs_llm_codegen=True,
        purpose="Генерируем код",
    )
    assert asyncio.run(executor.execute(step, "raw")) == "temp_result_v9"
    assert calls == [("Генерируем код", "raw", True)]
