"""Контекст хода (#197): последние ходы, история по запросу, воспоминания."""

import asyncio
import json
import sqlite3

import pytest

from app.config import settings
from app.core.agent.insight import InsightGenerator
from app.core.agent.planner import StepPlanner, describe_history, describe_turns
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.core.memory.session_memory import SessionMemory
from app.core.tools.read_history import NOT_FOUND
from app.core.tools.registry import tool_registry
from app.models.research_step import ResearchPlan, ResearchStep


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "sessions.sqlite"))
    return SessionMemory("ctx")


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute("CREATE TABLE sales AS SELECT 'msk' AS region, 10.0 AS revenue")
    yield manager
    manager.close()


class PromptSpy(FakeLLM):
    async def complete_structured(self, system, user, schema):
        self.prompt = user
        return await super().complete_structured(system, user, schema)


def answered(memory: SessionMemory, question: str, headline: str, status: str = "ok") -> None:
    memory.start_turn(question)
    memory.record_step(
        ResearchStep(step_id=0, tool="group_by", arguments={"by": ["region"]}, purpose="шаг")
    )
    memory.finish_turn(status, headline=headline if status == "ok" else None)


# --- История: find_turns и read_history ---


def test_find_turns_by_number_and_by_words(memory):
    answered(memory, "Какая выручка по регионам?", "Москва — 10 млн")
    answered(memory, "Сколько клиентов?", "1200 клиентов")
    answered(memory, "Выручку по месяцам покажи", "Пик в июле")
    memory.start_turn("Что было в первом вопросе?")

    assert [t["question"] for t in memory.find_turns(number=2)] == ["Сколько клиентов?"]
    # «выручку» и «выручка» совпадают по первым буквам; старые первыми.
    hits = memory.find_turns("выручка")
    assert [t["number"] for t in hits] == [1, 3]
    # Ищется и по ответу; идущий ход в историю не входит.
    assert [t["number"] for t in memory.find_turns("клиентов")] == [2]
    assert memory.find_turns("первом вопросе") == []
    assert memory.find_turns(number=4) == []


def test_read_history_step_returns_turns_older_than_context(memory, db):
    answered(memory, "Какая выручка по регионам?", "Москва — 10 млн")
    answered(memory, "Упал", "", status="failed")
    memory.start_turn("Что ты ответил про выручку?")

    executor = StepExecutor(db=db)
    step = ResearchStep(step_id=0, tool="read_history", arguments={"query": "выручка"})
    table = asyncio.run(executor.execute(step, "sales", history=memory))

    rows = db.to_records(table)
    assert [(r["turn"], r["question"], r["answer"]) for r in rows] == [
        (1, "Какая выручка по регионам?", "Москва — 10 млн")
    ]
    assert json.loads(rows[0]["plan"])[0]["tool"] == "group_by"

    by_number = ResearchStep(step_id=1, tool="read_history", arguments={"turn": 2})
    rows = db.to_records(asyncio.run(executor.execute(by_number, "sales", history=memory)))
    # План упавшего хода в историю не попадает, как и в контекст.
    assert [(r["question"], r["status"], r["plan"]) for r in rows] == [("Упал", "ход упал", "")]


def test_read_history_without_match_says_not_found(memory, db):
    memory.start_turn("Что было вчера?")
    step = ResearchStep(step_id=0, tool="read_history", arguments={"query": "вчера"})
    table = asyncio.run(StepExecutor(db=db).execute(step, "sales", history=memory))
    assert db.to_records(table)[0]["answer"] == NOT_FOUND


def test_read_history_is_a_lookup_that_needs_query_or_turn():
    tool = tool_registry.get("read_history")
    assert tool.kind == "lookup"
    assert "read_history" in tool_registry.describe()
    with pytest.raises(ValueError, match="query"):
        tool.parse_args([], {})
    with pytest.raises(TypeError, match="память сессии"):
        tool.run(None, "sales", [], turn=1)


def test_read_history_without_session_fails(db):
    step = ResearchStep(step_id=0, tool="read_history", arguments={"turn": 1})
    with pytest.raises(ValueError, match="история сессии недоступна"):
        asyncio.run(StepExecutor(db=db).execute(step, "sales"))


# --- Промпт планировщика: последние ходы ---


def test_prompt_shows_failed_turn_without_plan_and_points_to_older_turns():
    ok = {
        "number": 3,
        "status": "ok",
        "question": "Выручка по регионам",
        "steps": [{"step_id": 0, "tool": "group_by", "arguments": {"by": ["region"]}}],
        "headline": "Москва лидирует",
    }
    failed = {"number": 4, "status": "failed", "question": "Упал", "steps": [], "headline": None}
    text = describe_turns([ok, failed])
    assert "Ход 3. Вопрос: Выручка по регионам" in text
    assert "Ход 4. Вопрос: Упал\n   Ответ: не получен (ход упал)" in text
    assert text.count("План:") == 1

    history = describe_history({"turns": [ok, failed]})
    assert "Ходы 1–2 здесь не показаны" in history
    assert "read_history" in history
    assert "не показаны" not in describe_history({"turns": [{**ok, "number": 1}]})


# --- Воспоминания ---


def test_remember_persists_dedups_and_goes_to_context(memory):
    memory.start_turn("Запомни, что выручка без НДС")
    item = memory.remember("  выручка   без НДС ")
    assert item["text"] == "выручка без НДС"
    assert item["source"] == "user"
    assert item["enabled"] is True
    assert item["turn_id"] == memory.turn_id
    assert memory.remember("Выручка без НДС") == item
    assert memory.remember("   ") is None

    restored = SessionMemory("ctx")
    assert [m["text"] for m in restored.memories()] == ["выручка без НДС"]
    assert restored.build_context("?")["memories"] == ["выручка без НДС"]


def test_disabled_memory_stays_out_of_context(memory):
    memory.remember("выручка без НДС")
    memory.remember("клиенты — только юрлица")
    memory._state["memories"][0]["enabled"] = False
    assert memory.build_context("?")["memories"] == ["клиенты — только юрлица"]


def test_state_before_memories_opens_without_migration(memory):
    connection = sqlite3.connect(settings.sqlite_path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS session_state "
        "(session_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL)"
    )
    state = {"datasource": "sales", "pending_recovery": None, "turns": []}
    connection.execute("INSERT INTO session_state VALUES ('old', ?, 0)", (json.dumps(state),))
    connection.commit()
    connection.close()

    old = SessionMemory("old")
    assert old.memories() == []
    assert old.build_context("?")["memories"] == []
    old.remember("выручка без НДС")
    assert [m["text"] for m in SessionMemory("old").memories()] == ["выручка без НДС"]


def test_planner_prompt_has_all_memories_and_asks_for_remember():
    llm = PromptSpy()
    context = {"columns": ["region"], "memories": ["выручка без НДС", "клиенты — юрлица"]}
    asyncio.run(StepPlanner(llm=llm).plan("Выручка по регионам", context))
    assert "Воспоминания сессии" in llm.prompt
    assert "- выручка без НДС\n- клиенты — юрлица" in llm.prompt
    assert '"remember": [...]' in llm.prompt

    asyncio.run(StepPlanner(llm=llm).plan("Выручка по регионам", {"columns": ["region"]}))
    assert "Воспоминания сессии" not in llm.prompt


def test_plan_remember_accepts_string_and_null():
    assert ResearchPlan.model_validate({"remember": "выручка без НДС"}).remember == [
        "выручка без НДС"
    ]
    assert ResearchPlan.model_validate({"remember": None}).remember == []


def test_insight_prompt_has_memories(db):
    db.execute("CREATE TABLE temp_result_v1 AS SELECT 'msk' AS region, 10.0 AS revenue")
    llm = PromptSpy()
    gen = InsightGenerator(llm=llm, db=db)
    asyncio.run(gen.generate("Выручка?", "temp_result_v1", memories=["выручка без НДС"]))
    assert "- выручка без НДС" in llm.prompt
    asyncio.run(gen.generate("Выручка?", "temp_result_v1"))
    assert "Воспоминания сессии" not in llm.prompt


def test_set_memory_enabled_persists_and_unknown_is_none(memory):
    item = memory.remember("выручка без НДС")
    assert memory.set_memory_enabled(item["memory_id"], False)["enabled"] is False
    assert memory.set_memory_enabled("nope", True) is None
    restored = SessionMemory("ctx")
    assert restored.memories() == []
    assert [m["enabled"] for m in restored.memories(include_disabled=True)] == [False]
