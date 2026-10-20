"""Тесты персистентности ProfileMemory: паспорт переживает переоткрытие DuckDB."""

from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import profile_table
from app.core.memory.profile_memory import ProfileMemory


def test_profile_save_and_load(tmp_path):
    path = str(tmp_path / "main.duckdb")
    db = DuckDBManager(path)
    db.execute("CREATE TABLE raw AS SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS t(a, b)")
    ProfileMemory(db).save_profile(profile_table(db, "raw"), {"a": "номер"})
    db.close()

    reopened = DuckDBManager(path)
    try:
        memory = ProfileMemory(reopened)
        loaded = memory.load_profile("raw")
        assert [column.name for column in loaded.columns] == ["a", "b"]
        assert loaded.row_count == 2
        assert memory.load_glossary("raw") == {"a": "номер"}
    finally:
        reopened.close()


def test_profile_missing_table_returns_none(tmp_path):
    db = DuckDBManager(str(tmp_path / "main.duckdb"))
    try:
        assert ProfileMemory(db).load_profile("nonexistent_table") is None
    finally:
        db.close()
