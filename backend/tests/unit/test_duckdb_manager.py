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


def result_tables(db) -> set[str]:
    rows = db.query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name LIKE 'temp\\_result\\_v%' ESCAPE '\\'"
    )
    return {row["table_name"] for row in rows}


def age(db, name: str, seconds: float) -> None:
    db.execute(
        "UPDATE _meta.result_meta SET created_at = created_at - $seconds WHERE name = $name",
        {"seconds": seconds, "name": name},
    )


def test_save_step_result_records_creation_time(db):
    name = db.save_step_result("source", "SELECT 1 AS a")
    rows = db.query("SELECT created_at FROM _meta.result_meta WHERE name = $name", {"name": name})
    assert len(rows) == 1
    assert rows[0]["created_at"] > 0


def test_result_meta_stays_out_of_user_tables(db):
    db.save_step_result("source", "SELECT 1 AS a")
    db.cleanup_results(keep=set(), older_than_s=3600)
    rows = db.query(
        "SELECT schema_name, table_name FROM duckdb_tables() WHERE table_name = 'result_meta'"
    )
    assert rows == [{"schema_name": "_meta", "table_name": "result_meta"}]
    main_tables = db.query("SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'")
    assert all(row["table_name"].startswith("temp_result_v") for row in main_tables)


def test_cleanup_drops_old_results_but_keeps_fresh_and_kept(db):
    old = db.save_step_result("source", "SELECT 1 AS a")
    tile = db.save_step_result("source", "SELECT 2 AS a")
    fresh = db.save_step_result("source", "SELECT 3 AS a")
    age(db, old, 7200)
    age(db, tile, 7200)

    dropped = db.cleanup_results(keep={tile}, older_than_s=3600)

    assert dropped == [old]
    assert result_tables(db) == {tile, fresh}
    assert db.to_records(tile) == [{"a": 2}]


def test_cleanup_gives_untracked_results_a_full_ttl(db):
    db.execute("CREATE TABLE temp_result_v1 AS SELECT 1 AS a")

    assert db.cleanup_results(keep=set(), older_than_s=3600) == []
    assert result_tables(db) == {"temp_result_v1"}
    age(db, "temp_result_v1", 7200)
    assert db.cleanup_results(keep=set(), older_than_s=3600) == ["temp_result_v1"]


def test_cleanup_ignores_other_tables(db):
    db.execute("CREATE TABLE raw AS SELECT 1 AS a")
    db.cleanup_results(keep=set(), older_than_s=0)
    assert db.to_records("raw") == [{"a": 1}]


def test_numbers_of_dropped_results_are_not_reused(tmp_path):
    path = str(tmp_path / "main.duckdb")
    first = DuckDBManager(path)
    names = [first.save_step_result("raw", f"SELECT {index} AS a") for index in range(3)]
    for name in names:
        age(first, name, 7200)
    assert first.cleanup_results(keep=set(), older_than_s=3600) == names
    first.close()

    second = DuckDBManager(path)
    try:
        assert second.save_step_result("raw", "SELECT 9 AS a") == "temp_result_v4"
    finally:
        second.close()


def test_result_count_stays_bounded_over_fifty_questions(db):
    last_charts = []
    for question in range(50):
        steps = [
            db.save_step_result("source", f"SELECT {question} AS a") for step_index in range(3)
        ]
        last_charts = [*last_charts[-4:], steps[-1]]
        for name in steps:
            age(db, name, 3600)
        db.cleanup_results(keep=set(last_charts), older_than_s=1800)
        assert len(result_tables(db)) <= len(last_charts)
    assert result_tables(db) == set(last_charts)
