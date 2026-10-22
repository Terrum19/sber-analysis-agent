"""Unit-тесты SessionMemory: контекст, шаги, ответы, профиль."""

import pytest

from app.config import settings
from app.core.memory.session_memory import SessionMemory


@pytest.fixture
def memory(tmp_path):
    old_path = settings.sqlite_path
    settings.sqlite_path = str(tmp_path / "sessions.sqlite")
    mem = SessionMemory("test-session")
    yield mem
    settings.sqlite_path = old_path


def test_build_context_includes_profile(memory):
    ctx = memory.build_context("Тестовый вопрос")
    assert "messages" in ctx
    assert "summary" in ctx
    assert "question" in ctx
    assert ctx["question"] == "Тестовый вопрос"
    assert "columns" in ctx
    assert "columns_summary" in ctx
    assert "datasource" in ctx
    assert "profile" in ctx
    # Без DuckDB паспорта таблицы нет — планировщик работает без него.
    assert ctx["profile"] is None


def test_build_context_reads_profile_and_columns_from_passport(tmp_path):
    from app.core.data.duckdb_manager import DuckDBManager
    from app.core.data.profiling import profile_table
    from app.core.memory.profile_memory import ProfileMemory

    old_path = settings.sqlite_path
    settings.sqlite_path = str(tmp_path / "sessions3.sqlite")
    db = DuckDBManager(":memory:")
    try:
        db.execute("CREATE TABLE raw AS SELECT 'msk' AS region, 10.0 AS amount")
        ProfileMemory(db).save_profile(profile_table(db, "raw"))
        memory = SessionMemory("profile-session", db=db)
        memory.set_table("raw", [])
        ctx = memory.build_context("Выручка по регионам")
        assert ctx["profile"].table == "raw"
        assert ctx["columns"] == ["region", "amount"]
    finally:
        db.close()
        settings.sqlite_path = old_path


def test_record_step_and_answer(memory):
    from app.models.research_step import ResearchStep

    step = ResearchStep(
        step_id=0,
        tool="aggregate",
        columns=["category"],
        purpose="Тест шага",
    )
    memory.record_step(step, "temp_result_v1")
    memory.record_answer("Ответ агента")
    ctx = memory.build_context("Второй вопрос")
    assert "шаг 0: Тест шага" in ctx["summary"]


def test_table_is_none_until_set(memory):
    assert memory.table is None
    assert memory.build_context("Вопрос")["datasource"] is None


def test_set_table(memory):
    memory.set_table("raw", ["category", "revenue"])
    assert memory.table == "raw"
    assert memory._state["columns"] == ["category", "revenue"]
    assert SessionMemory("test-session").table == "raw"


def test_exists_after_set_table(memory):
    assert not memory.exists()
    memory.set_table("raw", ["category"])
    assert memory.exists()


def test_history_and_chart_are_kept_per_table(memory):
    memory.set_table("sales", ["revenue"])
    memory.record_chart("bar", "temp_result_v1", "Выручка")
    memory.record_answer("Ответ по продажам")
    memory.set_table("users", ["age"])
    assert memory.last_chart() is None
    assert memory.build_context("Вопрос")["messages"] == []
    memory.record_answer("Ответ по пользователям")
    memory.set_table("sales", ["revenue"])
    restored = SessionMemory("test-session")
    assert restored.last_chart()["result_table"] == "temp_result_v1"
    assert [m["content"] for m in restored.build_context("Вопрос")["messages"]] == [
        "Ответ по продажам"
    ]


def test_set_table_drops_steps_and_pending_recovery(memory):
    from app.models.research_step import ResearchStep

    memory.set_table("sales", ["revenue"])
    memory.record_step(ResearchStep(step_id=0, tool="aggregate", purpose="шаг"), "temp_result_v1")
    memory.set_pending_recovery({"failed_step_id": 0})
    memory.set_table("users", ["age"])
    assert memory.build_context("Вопрос")["summary"] == ""
    assert memory.get_pending_recovery() is None


def test_persist_and_load(tmp_path):
    old_path = settings.sqlite_path
    settings.sqlite_path = str(tmp_path / "sessions2.sqlite")
    try:
        mem1 = SessionMemory("persist-session")
        mem1.set_table("raw", ["a"])
        mem1.record_answer("Первый ответ")
        mem2 = SessionMemory("persist-session")
        ctx = mem2.build_context("Проверка")
        assert "Первый ответ" in ctx["messages"][-1]["content"]
    finally:
        settings.sqlite_path = old_path
