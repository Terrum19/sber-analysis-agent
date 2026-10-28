"""Тесты персистентности SessionMemory."""

from app.config import settings
from app.core.memory.session_memory import SessionMemory


def test_memory_file_created_and_loaded(tmp_path):
    old_path = settings.sqlite_path
    settings.sqlite_path = str(tmp_path / "persist.sqlite")
    try:
        mem1 = SessionMemory("persist-1")
        mem1.set_table("raw")
        mem1.start_turn("Запомни это")
        mem1.finish_turn("ok", headline="Запомнил")
        mem2 = SessionMemory("persist-1")
        assert mem2.table == "raw"
        assert mem2._state["turns"][-1]["question"] == "Запомни это"
    finally:
        settings.sqlite_path = old_path


def test_memory_different_sessions_isolated(tmp_path):
    old_path = settings.sqlite_path
    settings.sqlite_path = str(tmp_path / "isolated.sqlite")
    try:
        mem_a = SessionMemory("session-a")
        mem_a.set_table("raw")
        mem_a.start_turn("Только для а")
        mem_a.finish_turn("ok", headline="а")
        mem_b = SessionMemory("session-b")
        mem_b.set_table("raw")
        assert mem_b._state["turns"] == []
    finally:
        settings.sqlite_path = old_path
