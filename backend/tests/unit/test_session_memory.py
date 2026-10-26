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
    assert ctx["turns"] == []
    assert ctx["question"] == "Тестовый вопрос"
    assert ctx["columns"] == []
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
        memory.set_table("raw")
        ctx = memory.build_context("Выручка по регионам")
        assert ctx["profile"].table == "raw"
        assert ctx["columns"] == ["region", "amount"]
    finally:
        db.close()
        settings.sqlite_path = old_path


def test_table_is_none_until_set(memory):
    assert memory.table is None
    assert memory.build_context("Вопрос")["datasource"] is None


def test_exists_after_set_table(memory):
    assert not memory.exists()
    memory.set_table("raw")
    assert memory.exists()


def step(step_id: int = 0, tool: str = "group_by", **arguments):
    from app.models.research_step import ResearchStep

    return ResearchStep(step_id=step_id, tool=tool, arguments=arguments, purpose=f"шаг {tool}")


def answered(memory, question: str, status: str = "ok", chart: str | None = None) -> str:
    """Ход с одним шагом: вопрос -> шаг -> (график) -> итог со статусом."""
    turn_id = memory.start_turn(question)
    memory.record_step(step(by=["region"]))
    if chart is not None:
        memory.record_chart("bar", chart, f"График: {question}")
    memory.finish_turn(
        status,
        headline=f"Ответ: {question}",
        result_table="temp_result_v1",
        result_columns=["region", "sum_revenue"],
    )
    return turn_id


def test_turn_keeps_question_plan_and_answer(memory):
    turn_id = answered(memory, "Выручка по регионам")
    turn = SessionMemory("test-session").build_context("Дальше")["turns"][0]
    assert turn["turn_id"] == turn_id
    assert turn["question"] == "Выручка по регионам"
    assert turn["headline"] == "Ответ: Выручка по регионам"
    assert turn["result_columns"] == ["region", "sum_revenue"]
    assert turn["steps"] == [
        {
            "step_id": 0,
            "tool": "group_by",
            "input": "$prev",
            "arguments": {"by": ["region"]},
            "columns": [],
            "purpose": "шаг group_by",
        }
    ]


def test_failed_and_clarification_turns_stay_out_of_context(memory):
    answered(memory, "Выручка по регионам")
    answered(memory, "Упал", status="failed")
    answered(memory, "Нужно уточнение", status="clarification")
    # Ход без finish_turn (сбой процесса) остаётся failed.
    memory.start_turn("Оборвался")
    memory.record_step(step())
    restored = SessionMemory("test-session")
    assert [t["question"] for t in restored.build_context("?")["turns"]] == ["Выручка по регионам"]
    assert restored._state["turns"][-1]["status"] == "failed"


def test_turns_without_plan_stay_out_of_context(memory):
    memory.start_turn("Привет")
    memory.finish_turn("ok", headline="Здравствуйте")
    assert memory.build_context("?")["turns"] == []


def test_context_does_not_grow_past_three_turns(memory):
    for number in range(6):
        answered(memory, f"Вопрос {number}")
    turns = memory.build_context("?")["turns"]
    assert [t["question"] for t in turns] == ["Вопрос 3", "Вопрос 4", "Вопрос 5"]


def test_resume_turn_continues_the_same_turn(memory):
    turn_id = memory.start_turn("Выручка по регионам")
    memory.record_step(step(0))
    memory.finish_turn("clarification")

    restored = SessionMemory("test-session")
    assert restored.resume_turn(turn_id)
    restored.record_step(step(1, "sort_limit"))
    restored.finish_turn("ok", headline="Москва")
    turns = SessionMemory("test-session")._state["turns"]
    assert len(turns) == 1
    assert [s["step_id"] for s in turns[0]["steps"]] == [0, 1]
    assert not restored.resume_turn("unknown")


def test_last_chart_is_from_the_last_answered_turn_with_chart(memory):
    assert memory.last_chart() is None
    answered(memory, "Выручка", chart="temp_result_v1")
    answered(memory, "Без графика")
    answered(memory, "Упал", status="failed", chart="temp_result_v9")
    assert SessionMemory("test-session").last_chart() == {
        "type": "bar",
        "title": "График: Выручка",
        "result_table": "temp_result_v1",
    }


def test_set_table_keeps_turns_and_drops_pending_recovery(memory):
    memory.set_table("sales")
    answered(memory, "Выручка по регионам")
    memory.set_pending_recovery({"failed_step_id": 0})
    memory.set_table("users")
    assert memory.table == "users"
    assert len(memory.build_context("?")["turns"]) == 1
    assert memory.get_pending_recovery() is None


def test_turn_records_the_default_table(memory):
    memory.start_turn("Без таблицы")
    memory.set_table("sales")
    memory.start_turn("По продажам")
    assert [t["tables"] for t in memory._state["turns"]] == [[], ["sales"]]


def test_state_before_turns_still_opens(memory):
    import json
    import sqlite3

    old_state = {
        "datasource": "sales",
        "columns": ["region"],
        "steps": [{"step_id": 0, "purpose": "шаг", "result": "temp_result_v1"}],
        "tables": {
            "sales": {"messages": [{"role": "agent", "content": "Ответ"}], "last_chart": None}
        },
        "pending_recovery": None,
        "title": "Старая",
    }
    connection = sqlite3.connect(settings.sqlite_path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS session_state "
        "(session_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL)"
    )
    connection.execute("INSERT INTO session_state VALUES (?, ?, 0)", ("old", json.dumps(old_state)))
    connection.commit()
    connection.close()

    old = SessionMemory("old")
    assert old.table == "sales"
    assert old.title == "Старая"
    assert old.build_context("?")["turns"] == []
    assert old.last_chart() is None
    answered(old, "Новый вопрос")
    assert len(SessionMemory("old").build_context("?")["turns"]) == 1


def replied(memory, question: str, reply_to: str | None, chart: str | None = None) -> str:
    turn_id = memory.start_turn(question, reply_to=reply_to)
    memory.record_step(step(by=["month"]))
    if chart is not None:
        memory.record_chart("line", chart, question)
    memory.finish_turn("ok", headline=f"Ответ: {question}")
    return turn_id


def test_reply_gets_chain_instead_of_recent_turns(memory):
    first = answered(memory, "Выручка по регионам")
    answered(memory, "Сколько пользователей?")
    second = replied(memory, "А только Москва?", reply_to=first)
    memory.start_turn("А за 2023?", reply_to=second)
    context = memory.build_context("А за 2023?")
    assert [t["question"] for t in context["reply"]] == ["Выручка по регионам", "А только Москва?"]
    assert context["turns"] == []


def test_reply_chain_is_limited_to_three_turns(memory):
    turn_id = answered(memory, "Вопрос 0")
    for number in range(1, 5):
        turn_id = replied(memory, f"Вопрос {number}", reply_to=turn_id)
    memory.start_turn("Дальше", reply_to=turn_id)
    assert [t["question"] for t in memory.reply_chain()] == ["Вопрос 2", "Вопрос 3", "Вопрос 4"]


def test_reply_to_unknown_or_failed_turn_falls_back_to_recent_turns(memory):
    answered(memory, "Выручка по регионам")
    failed = answered(memory, "Упал", status="failed")
    for reply_to in ["unknown", failed]:
        memory.start_turn("А только Москва?", reply_to=reply_to)
        context = memory.build_context("А только Москва?")
        assert context["reply"] == []
        assert [t["question"] for t in context["turns"]] == ["Выручка по регионам"]


def test_reply_takes_chart_of_the_chosen_turn(memory):
    first = answered(memory, "Выручка", chart="temp_result_v1")
    answered(memory, "Пользователи", chart="temp_result_v2")
    memory.start_turn("Покажи таблицей", reply_to=first)
    assert memory.last_chart()["result_table"] == "temp_result_v1"
    memory.start_turn("Покажи таблицей")
    assert memory.last_chart()["result_table"] == "temp_result_v2"
