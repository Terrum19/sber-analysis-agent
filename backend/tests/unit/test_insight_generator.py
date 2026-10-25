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
