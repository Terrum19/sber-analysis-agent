"""Быстрый и запасной инсайт: одна ячейка не требует LLM."""

import time

from app.core.agent.insight import InsightGenerator
from app.core.agent.orchestrator import AgentOrchestrator, Execution
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.models.research_step import ResearchPlan


class CountingLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def complete_structured(self, system, user, schema):
        self.calls += 1
        return await super().complete_structured(system, user, schema)


def test_one_number_is_answered_without_llm() -> None:
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE temp_result_v1 AS SELECT 42 AS total")
    llm = CountingLLM()
    insight = InsightGenerator(llm=llm, db=db).fast_insight("Сколько строк?", "temp_result_v1")
    assert insight is not None
    assert "42" in insight.headline
    assert llm.calls == 0
    db.close()


def test_series_or_text_result_goes_to_llm() -> None:
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE temp_result_v1 AS SELECT 1 AS a UNION ALL SELECT 2 AS a")
    db.execute("CREATE TABLE temp_result_v2 AS SELECT 'msk' AS region")
    gen = InsightGenerator(llm=CountingLLM(), db=db)
    assert gen.fast_insight("Сколько?", "temp_result_v1") is None
    assert gen.fast_insight("Где?", "temp_result_v2") is None
    db.close()


def test_fallback_insight_reports_partial_result() -> None:
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE temp_result_v1 AS SELECT 7 AS value")
    gen = InsightGenerator(llm=CountingLLM(), db=db)
    insight = gen.fallback_insight("Сколько?", "temp_result_v1", error="бюджет")
    assert insight.headline == "Результат готов"
    assert insight.limits == "бюджет"
    missing = gen.fallback_insight("Сколько?", "no_such_table", error="бюджет")
    assert "Не удалось завершить" in missing.headline
    db.close()


async def test_orchestrator_uses_fast_insight_and_never_calls_generate() -> None:
    class FailingGenerate(InsightGenerator):
        async def generate(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("LLM-инсайт не должен вызываться")

    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE temp_result_v1 AS SELECT 3 AS total")
    planner = object()
    agent = AgentOrchestrator(
        planner=planner,
        executor=object(),
        insight_gen=FailingGenerate(llm=CountingLLM(), db=db),
        db=db,
    )
    execution = Execution(
        result_table="temp_result_v1",
        plan=ResearchPlan(steps=[]),
    )
    started = time.perf_counter()
    insight = await agent._insight("Сколько строк?", execution)
    assert insight.headline.startswith("Сколько строк")
    assert time.perf_counter() - started < 1
    db.close()
