"""Тесты персистентности SessionMemory."""

import os
import tempfile
import pytest

from app.core.memory.session_memory import SessionMemory
from app.config import settings


def test_memory_file_created_and_loaded(tmp_path):
    old_path = settings.sqlite_path
    settings.sqlite_path = str(tmp_path / "persist.sqlite")
    try:
        mem1 = SessionMemory("persist-1")
        mem1.record_answer("Запомни это")
        mem2 = SessionMemory("persist-1")
        ctx = mem2.build_context("Проверка персистентности")
        assert "Запомни это" in ctx["messages"][-1]["content"]
    finally:
        settings.sqlite_path = old_path


def test_memory_different_sessions_isolated(tmp_path):
    old_path = settings.sqlite_path
    settings.sqlite_path = str(tmp_path / "isolated.sqlite")
    try:
        mem_a = SessionMemory("session-a")
        mem_a.record_answer("Только для а")
        mem_b = SessionMemory("session-b")
        ctx_b = mem_b.build_context("Вопрос б")
        assert "Только для а" not in str(ctx_b["messages"])
    finally:
        settings.sqlite_path = old_path
