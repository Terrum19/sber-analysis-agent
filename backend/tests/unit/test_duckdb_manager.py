"""Unit-тесты DuckDBManager: запросы, temp-таблицы, загрузка CSV."""

import pytest

from app.core.data.duckdb_manager import DuckDBManager


@pytest.fixture
def db():
    return DuckDBManager(path=":memory:")


def test_query_select(db):
    db.execute("CREATE TABLE t AS SELECT 1 AS x UNION ALL SELECT 2 AS x")
    rows = db.query("SELECT x FROM t")
    assert len(rows) == 2
    assert rows[0]["x"] == 1


def test_execute_ddl(db):
    db.execute("CREATE TABLE t2 AS SELECT 42 AS val")
    rows = db.query("SELECT val FROM t2")
    assert rows[0]["val"] == 42


def test_save_step_result(db):
    db.execute("CREATE TABLE source AS SELECT 1 AS a UNION ALL SELECT 2 AS a")
    name = db.save_step_result("source", "SELECT * FROM source")
    assert name.startswith("temp_result_v")
    rows = db.to_records(name)
    assert len(rows) == 2


def test_to_records_limit(db):
    db.execute("CREATE TABLE big AS SELECT i AS v FROM range(100) t(i)")
    rows = db.to_records("big", limit=5)
    assert len(rows) == 5


def test_save_step_result_continues_numbering_of_existing_file(tmp_path):
    """Новый менеджер (новый запрос) не перезаписывает temp_result_v1 прошлого хода."""
    path = str(tmp_path / "main.duckdb")
    first = DuckDBManager(path)
    first.execute("CREATE TABLE raw AS SELECT 1 AS x")
    assert first.save_step_result("raw", "SELECT 1 AS x") == "temp_result_v1"
    first.close()

    second = DuckDBManager(path)
    try:
        assert second.save_step_result("raw", "SELECT 2 AS x") == "temp_result_v2"
        assert second.to_records("temp_result_v1") == [{"x": 1}]
    finally:
        second.close()
