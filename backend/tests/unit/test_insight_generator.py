"""Unit-тесты InsightGenerator: генерация инсайта."""

import asyncio

import pytest

from app.core.agent.insight import InsightGenerator
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.models.insight import Insight


@pytest.fixture
def db():
    manager = DuckDBManager(path=":memory:")
    manager.execute("CREATE TABLE temp_result_v1 AS SELECT 1 AS a UNION ALL SELECT 2 AS a")
    manager.execute("CREATE TABLE temp_result_v2 AS SELECT 1 AS a WHERE 1=0")
    return manager


def test_generate_returns_insight(db):
    gen = InsightGenerator(llm=FakeLLM(), db=db)
    insight = asyncio.run(gen.generate("Тест", "temp_result_v1"))
    assert isinstance(insight, Insight)
    assert insight.headline == "Исследование завершено"
    assert len(insight.details) > 0


def test_generate_empty_table(db):
    gen = InsightGenerator(llm=FakeLLM(), db=db)
    insight = asyncio.run(gen.generate("Пустая", "temp_result_v2"))
    assert isinstance(insight, Insight)


class PromptSpy(FakeLLM):
    async def complete_structured(self, system, user, schema):
        self.prompt = user
        return await super().complete_structured(system, user, schema)


def test_prompt_knows_how_the_result_was_obtained(db):
    from app.models.research_step import ResearchStep

    db.execute("CREATE TABLE temp_result_v3 AS SELECT 41 AS n")
    llm = PromptSpy()
    steps = [
        ResearchStep(
            step_id=0,
            tool="filter_rows",
            arguments={"column": "amount", "operator": ">", "value": 150},
            purpose="Сумма больше 150",
        ),
        ResearchStep(
            step_id=1,
            tool="group_by",
            arguments={"by": [], "function": "COUNT"},
            purpose="Посчитать события",
        ),
    ]
    asyncio.run(
        InsightGenerator(llm=llm, db=db).generate("А из них?", "temp_result_v3", steps=steps)
    )
    assert (
        '1. Сумма больше 150 — filter_rows {"column": "amount", "operator": ">", "value": 150}'
        in llm.prompt
    )
    assert "2. Посчитать события — group_by" in llm.prompt
    assert "одна строка" in llm.prompt


def test_series_prompt_asks_for_whole_series(db):
    llm = PromptSpy()
    asyncio.run(InsightGenerator(llm=llm, db=db).generate("По месяцам", "temp_result_v1"))
    assert "Результат — 2 строк" in llm.prompt
    assert "12 345" not in llm.prompt


def test_series_shape_counts_the_whole_table(db):
    db.execute("CREATE TABLE temp_result_v4 AS SELECT range AS id FROM range(471)")
    llm = PromptSpy()
    asyncio.run(InsightGenerator(llm=llm, db=db).generate("Сколько?", "temp_result_v4"))
    assert "Результат — 471 строк" in llm.prompt
