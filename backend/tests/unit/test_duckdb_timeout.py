"""SQL-таймаут DuckDB: interrupt, освобождение lock и живое соединение."""

import time

import duckdb
import pytest

from app.core.data.duckdb_manager import DuckDBManager

LONG_SQL = "SELECT count(*) AS n FROM range(100000000000) a, range(10) b"


def test_interrupt_stops_a_long_query_and_keeps_the_connection_alive() -> None:
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE t AS SELECT range AS x FROM range(10)")
    started = time.perf_counter()
    with pytest.raises(duckdb.InterruptException):
        db.query(LONG_SQL, timeout_s=0.2)
    assert time.perf_counter() - started < 5
    # Lock освобождён, соединение работает.
    acquired = db.lock.acquire(timeout=1)
    try:
        assert acquired
    finally:
        if acquired:
            db.lock.release()
    assert db.query("SELECT count(*) AS n FROM t") == [{"n": 10}]
    db.close()


def test_interrupted_save_step_result_does_not_break_the_connection() -> None:
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE t AS SELECT range AS x FROM range(10)")
    with pytest.raises(duckdb.InterruptException):
        db.save_step_result("t", LONG_SQL, timeout_s=0.2)
    # Временная таблица не должна остаться после прерывания.
    rows = db.query(
        "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'temp_result_v%'"
    )
    assert rows == []
    assert db.query("SELECT 1 AS ok") == [{"ok": 1}]
    db.close()
