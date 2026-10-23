"""Прототип исправлений качества за флагом agent_quality_fixes."""

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


def test_group_by_without_keys_gives_total(db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_quality_fixes", True)
    result = tool_registry.get("group_by").run(db, "t", [], by=[], column="amount", function="SUM")
    assert db.to_records(result) == [{"sum_amount": sum(range(120)) * 10}]


def test_group_by_without_keys_still_fails_when_flag_off(db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_quality_fixes", False)
    with pytest.raises(ValueError):
        tool_registry.get("group_by").run(db, "t", [], by=[], column="amount", function="SUM")


def test_insight_facts_count_whole_table(db) -> None:
    facts = insight_facts(db, "t", shown=50)
    assert "строк в результате: 120 (показано модели: 50)" in facts
    assert "amount: сумма 71400" in facts


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


def test_time_bucket_avg_without_metric_fails_validation() -> None:
    tool = tool_registry.get("time_bucket")
    with pytest.raises(ValueError):
        tool.parse_args([], {"date_column": "d", "function": "AVG"})


def test_context_only_for_follow_up_questions() -> None:
    from app.core.agent.quality_fixes import frame_context

    summary = "шаг 0: Оставить события с суммой больше 150"
    assert frame_context(summary, "Есть ли выбросы в amount?") == "нет"
    assert "ПРОШЛЫХ" in frame_context(summary, "А из них сколько в марте?")


def test_select_tools_keeps_core_and_matches_question() -> None:
    from app.core.agent.quality_fixes import select_tools

    text = select_tools("Есть ли тренд выручки?")
    assert "- group_by" in text and "- trend_analysis" in text
    assert "- funnel_steps" not in text
