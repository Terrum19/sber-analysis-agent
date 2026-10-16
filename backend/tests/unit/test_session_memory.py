"""Unit-тесты SessionMemory: контекст, шаги, ответы, профиль."""

import json
import os
import pytest

from app.core.memory.session_memory import SessionMemory
from app.config import settings


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
    assert isinstance(ctx["profile"], dict)


def test_record_step_and_answer(memory):
    from app.models.research_step import ResearchStep
    step = ResearchStep(
        step_id=0,
        tool="aggregate",
        columns=["category"],
        needs_llm_codegen=False,
        purpose="Тест шага",
    )
    memory.record_step(step, "temp_result_v1")
    memory.record_answer("Ответ агента")
    ctx = memory.build_context("Второй вопрос")
    assert "шаг 0: Тест шага" in ctx["summary"]


def test_set_datasource(memory):
    memory.set_datasource("raw", ["category", "revenue"])
    assert memory._state["datasource"] == "raw"
    assert memory._state["columns"] == ["category", "revenue"]


def test_persist_and_load(tmp_path):
    old_path = settings.sqlite_path
    settings.sqlite_path = str(tmp_path / "sessions2.sqlite")
    try:
        mem1 = SessionMemory("persist-session")
        mem1.record_answer("Первый ответ")
        mem2 = SessionMemory("persist-session")
        ctx = mem2.build_context("Проверка")
        assert "Первый ответ" in ctx["messages"][-1]["content"]
    finally:
        settings.sqlite_path = old_path
