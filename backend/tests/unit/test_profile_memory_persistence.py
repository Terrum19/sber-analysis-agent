"""Тесты персистентности ProfileMemory."""

import tempfile
import pytest

from app.core.memory.profile_memory import ProfileMemory
from app.config import settings


def test_profile_save_and_load(tmp_path):
    old_dir = settings.storage_dir
    settings.storage_dir = str(tmp_path)
    try:
        pm = ProfileMemory()
        pm.save("raw", {"columns": ["a", "b"], "rows": 100})
        loaded = pm.load("raw")
        assert loaded["columns"] == ["a", "b"]
        assert loaded["rows"] == 100
    finally:
        settings.storage_dir = old_dir


def test_profile_missing_table_returns_empty(tmp_path):
    old_dir = settings.storage_dir
    settings.storage_dir = str(tmp_path)
    try:
        pm = ProfileMemory()
        loaded = pm.load("nonexistent_table")
        assert loaded == {}
    finally:
        settings.storage_dir = old_dir
