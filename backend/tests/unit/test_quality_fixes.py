"""Исправления качества за флагом agent_quality_fixes."""

import pytest

from app.config import settings
from app.core.agent.quality_fixes import insight_facts
from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.registry import tool_registry


@pytest.fixture
def db(tmp_path) -> DuckDBManager:
    manager = DuckDBManager(str(tmp_path / "q.duckdb"))
    manager.query("CREATE TABLE t AS SELECT range AS id, range * 10 AS amount FROM range(120)")
    return manager


def test_group_by_without_keys_gives_total(db) -> None:
    result = tool_registry.get("group_by").run(db, "t", [], by=[], column="amount", function="SUM")
    assert db.to_records(result) == [{"sum_amount": sum(range(120)) * 10}]


def test_insight_facts_count_whole_table(db) -> None:
    facts = insight_facts(db, "t", shown=50)
    assert "строк в результате: 120 (показано модели: 50)" in facts
    assert "amount: сумма 71400" in facts


def test_insight_facts_skip_non_numeric_types(db) -> None:
    # INTERVAL содержит «INT», но SUM по нему DuckDB не считает.
    db.query("CREATE TABLE w AS SELECT INTERVAL 3 DAY AS d, 'a' AS s, 1.5::DECIMAL(4,1) AS x")
    facts = insight_facts(db, "w", shown=50)
    assert "- d:" not in facts and "- s:" not in facts
    assert "x: сумма 1.5" in facts


def test_tool_hints_only_with_flag(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_quality_fixes", False)
    assert "НЕ сумма" not in tool_registry.describe()
    monkeypatch.setattr(settings, "agent_quality_fixes", True)
    assert "НЕ сумма" in tool_registry.describe()


def test_ungrounded_numbers_detects_miscopied_count() -> None:
    from app.core.agent.quality_fixes import ungrounded_numbers

    prompt = "Вопрос: больше 150?\n- строк в результате: 471 (показано модели: 50)"
    assert ungrounded_numbers("Событий больше 150 — 468", prompt) == ["468"]
    assert ungrounded_numbers("Событий больше 150 — 471", prompt) == []
    assert ungrounded_numbers("Сумма — 271 050", "сумма 271050") == []


def test_ungrounded_numbers_allows_rounding_and_shares() -> None:
    from app.core.agent.quality_fixes import ungrounded_numbers

    assert ungrounded_numbers("Среднее — 12.3", "среднее 12.34") == []
    assert ungrounded_numbers("Среднее — 12", "среднее 12.34") == []
    assert ungrounded_numbers("Среднее — 12.4", "среднее 12.34") == ["12.4"]
    assert ungrounded_numbers("Москва — 40% событий", "[{'n': 4}, {'n': 6}]") == []


def test_time_bucket_avg_without_metric_fails_validation() -> None:
    tool = tool_registry.get("time_bucket")
    with pytest.raises(ValueError):
        tool.parse_args([], {"date_column": "d", "function": "AVG"})


def test_select_tools_keeps_core_and_matches_question() -> None:
    from app.core.agent.quality_fixes import select_tools

    text = select_tools("Есть ли тренд выручки?")
    assert "- group_by" in text and "- trend_analysis" in text
    assert "- funnel_steps" not in text


class _ScriptedLLM:
    """complete_structured отдаёт заготовленные заголовки по очереди."""

    def __init__(self, *headlines: str) -> None:
        self.headlines = list(headlines)
        self.prompts: list[str] = []

    async def complete_structured(self, system, user, schema):
        self.prompts.append(user)
        return schema(headline=self.headlines.pop(0))


async def test_insight_asks_again_for_ungrounded_number(db, monkeypatch) -> None:
    from app.core.agent.insight import InsightGenerator

    monkeypatch.setattr(settings, "agent_quality_fixes", True)
    llm = _ScriptedLLM("Строк — 118", "Строк — 120")
    insight = await InsightGenerator(llm=llm, db=db).generate("Сколько строк?", "t")
    assert insight.headline == "Строк — 120"
    assert len(llm.prompts) == 2 and "118" in llm.prompts[1]


async def test_insight_without_facts_when_they_fail(db, monkeypatch) -> None:
    from app.core.agent import insight as module

    def broken(*args, **kwargs):
        raise RuntimeError("binder error")

    monkeypatch.setattr(settings, "agent_quality_fixes", True)
    monkeypatch.setattr(module, "insight_facts", broken)
    llm = _ScriptedLLM("Строк в таблице — 120")
    insight = await module.InsightGenerator(llm=llm, db=db).generate("Сколько строк?", "t")
    assert insight.headline == "Строк в таблице — 120"
    assert len(llm.prompts) == 1


def test_insight_facts_name_the_rows_of_min_and_max(db) -> None:
    db.query(
        "CREATE TABLE m AS SELECT * FROM (VALUES "
        "(TIMESTAMP '2024-01-01', 26484), (TIMESTAMP '2024-08-01', 19362), "
        "(TIMESTAMP '2024-10-01', 21833)) AS v(period, sum_amount)"
    )
    facts = insight_facts(db, "m", shown=50)
    assert "мин 19362 (period = 2024-08-01)" in facts
    assert "макс 26484 (period = 2024-01-01)" in facts


def test_single_row_facts_have_no_labels(db) -> None:
    db.query("CREATE TABLE one AS SELECT 'all' AS k, 5 AS n")
    assert "(k =" not in insight_facts(db, "one", shown=50)


def test_rows_are_shown_as_compact_table() -> None:
    from datetime import date, datetime

    from app.core.agent.quality_fixes import format_rows

    rows = [
        {"period": datetime(2024, 1, 1), "avg": 542.1666, "day": date(2024, 3, 5)},
        {"period": datetime(2024, 2, 1, 12, 30), "avg": 7, "day": date(2024, 3, 6)},
    ]
    assert format_rows(rows) == (
        "period | avg | day\n2024-01-01 | 542.17 | 2024-03-05\n2024-02-01 12:30:00 | 7 | 2024-03-06"
    )
    assert format_rows([]) == "пусто"
