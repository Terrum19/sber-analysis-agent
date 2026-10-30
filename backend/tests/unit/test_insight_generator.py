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


def test_tile_insight_sends_title_view_and_rows(db):
    class RecordingLLM(FakeLLM):
        def __init__(self) -> None:
            super().__init__()
            self.prompts: list[str] = []

        async def complete(self, system, user, **kwargs):
            self.prompts.append(user)
            return "  Значения растут.\n"

    db.execute("CREATE TABLE temp_result_v3 AS SELECT range AS a FROM range(100)")
    llm = RecordingLLM()
    gen = InsightGenerator(llm=llm, db=db)
    text = asyncio.run(gen.generate_tile_insight("Рост", "temp_result_v3", "line"))
    assert text == "Значения растут."
    assert "«Рост»" in llm.prompts[0]
    assert "line" in llm.prompts[0]
    assert "{'a': 29}" in llm.prompts[0]
    assert "{'a': 30}" not in llm.prompts[0]


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


def test_trend_result_asks_for_growth_and_significance(db):
    llm = PromptSpy()
    asyncio.run(
        InsightGenerator(llm=llm, db=db).generate(
            "Растёт ли?", "temp_result_v1", result_tool="trend_analysis"
        )
    )
    assert "yoy_change_avg" in llm.prompt and "p_value < 0,05" in llm.prompt
    assert "Результат — 2 строк" not in llm.prompt


def test_series_shape_counts_the_whole_table(db):
    db.execute("CREATE TABLE temp_result_v4 AS SELECT range AS id FROM range(471)")
    llm = PromptSpy()
    asyncio.run(InsightGenerator(llm=llm, db=db).generate("Сколько?", "temp_result_v4"))
    assert "Результат — 471 строк" in llm.prompt


def test_prompt_asks_for_a_full_sentence_not_a_bare_number(db):
    llm = PromptSpy()
    asyncio.run(InsightGenerator(llm=llm, db=db).generate("Сколько?", "temp_result_v1"))
    assert "Голое число или" in llm.prompt


def test_numbers_from_plan_conditions_are_grounded(db):
    from app.models.insight import Insight
    from app.models.research_step import ResearchStep

    class Scripted(PromptSpy):
        calls = 0

        async def complete_structured(self, system, user, schema):
            self.calls += 1
            return Insight(headline="В декабре 2024 событий с суммой > 150 — 41", details=[])

    db.execute("CREATE TABLE temp_result_v5 AS SELECT 41 AS n")
    steps = [
        ResearchStep(
            step_id=0,
            tool="filter_rows",
            arguments={"column": "amount", "operator": ">", "value": 150},
            purpose="Сумма больше 150 в декабре 2024",
        )
    ]
    llm = Scripted()
    asyncio.run(
        InsightGenerator(llm=llm, db=db).generate("Сколько?", "temp_result_v5", steps=steps)
    )
    assert llm.calls == 1


@pytest.fixture(params=[True, False], ids=["quality_fixes", "plain"])
def fixes(request, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "agent_quality_fixes", request.param)


def test_prompt_without_tests_has_no_significance_rule(db, fixes):
    llm = PromptSpy()
    asyncio.run(InsightGenerator(llm=llm, db=db).generate("Сколько вин?", "temp_result_v1"))
    for text in ("стат-тест", "значим", "0.05", "Сопутствующие"):
        assert text not in llm.prompt


def test_p_value_in_result_keeps_threshold(db, fixes):
    db.execute("CREATE TABLE temp_test AS SELECT 0.42 AS correlation, 1e-30 AS p_value, 7 AS n")
    llm = PromptSpy()
    asyncio.run(InsightGenerator(llm=llm, db=db).generate("Связаны ли?", "temp_test"))
    assert "p_value < 0.05" in llm.prompt
    assert "Сопутствующие" not in llm.prompt


def test_supporting_test_brings_both_parts(db, fixes):
    db.execute("CREATE TABLE temp_test AS SELECT 2.1 AS statistic, 0.03 AS P_VALUE")
    llm = PromptSpy()
    supporting = [("t-тест", "temp_test")]
    asyncio.run(
        InsightGenerator(llm=llm, db=db).generate("Различаются?", "temp_result_v1", supporting)
    )
    assert "Сопутствующие результаты" in llm.prompt
    assert "p_value < 0.05" in llm.prompt


def test_supporting_without_p_value_has_no_threshold(db, fixes):
    db.execute("CREATE TABLE temp_stats AS SELECT 5.0 AS mean, 1.2 AS std")
    llm = PromptSpy()
    supporting = [("Статистики", "temp_stats")]
    asyncio.run(
        InsightGenerator(llm=llm, db=db).generate("Какие цены?", "temp_result_v1", supporting)
    )
    assert "Сопутствующие результаты" in llm.prompt
    assert "0.05" not in llm.prompt


class Headline(PromptSpy):
    calls = 0

    async def complete_structured(self, system, user, schema):
        self.calls += 1
        return Insight(headline="Цена и оценка связаны: 0,42, значимо (p < 0,05)", details=[])


def test_threshold_from_prompt_is_grounded(db):
    db.execute("CREATE TABLE temp_test AS SELECT 0.42 AS correlation, 1e-30 AS p_value, 7 AS n")
    llm = Headline()
    asyncio.run(InsightGenerator(llm=llm, db=db).generate("Связаны ли?", "temp_test"))
    assert llm.calls == 1


def test_threshold_without_test_is_not_grounded(db):
    db.execute("CREATE TABLE temp_corr AS SELECT 0.42 AS correlation, 7 AS n")
    llm = Headline()
    asyncio.run(InsightGenerator(llm=llm, db=db).generate("Связаны ли?", "temp_corr"))
    assert llm.calls == 2
