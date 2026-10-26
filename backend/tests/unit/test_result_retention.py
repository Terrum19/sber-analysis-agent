"""Unit-тесты очистки temp_result_vN: набор keep, проход очистки, запуск при старте."""

import json
import sqlite3
import time

from fastapi.testclient import TestClient

from app.config import settings
from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.result_retention import cleanup, collect_keep
from app.core.memory.session_memory import SessionMemory
from app.main import app


def make_dashboard(result_table: str) -> Dashboard:
    tile = DashboardTile(
        title="Выручка",
        spec={"mark": "bar"},
        result_table=result_table,
        snapshot_data=[{"region": "msk", "value": 1}],
        snapshot_at=100.0,
    )
    return Dashboard(session_id="s1", title="Продажи", tiles=[tile])


def chart_session(session_id: str, charts: dict[str, str]) -> None:
    memory = SessionMemory(session_id)
    for table, result_table in charts.items():
        memory.set_table(table)
        memory.start_turn("Выручка по регионам")
        memory.record_chart("bar", result_table)
        memory.finish_turn("ok", headline="готово")


def backdate_session(session_id: str, seconds: float) -> None:
    connection = sqlite3.connect(settings.sqlite_path)
    try:
        connection.execute(
            "UPDATE session_state SET updated_at = ? WHERE session_id = ?",
            (time.time() - seconds, session_id),
        )
        connection.commit()
    finally:
        connection.close()


def age_all(db: DuckDBManager, seconds: float) -> None:
    db.execute(
        "UPDATE _meta.result_meta SET created_at = created_at - $seconds", {"seconds": seconds}
    )


def result_tables(db: DuckDBManager) -> set[str]:
    rows = db.query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name LIKE 'temp\\_result\\_v%' ESCAPE '\\'"
    )
    return {row["table_name"] for row in rows}


def test_collect_keep_without_sqlite_file_is_empty(tmp_path):
    assert collect_keep(str(tmp_path / "missing.sqlite")) == set()


def test_collect_keep_takes_tiles_and_charts_of_every_table_of_recent_sessions():
    DashboardStore().create(make_dashboard("temp_result_v1"))
    chart_session("fresh", {"sales": "temp_result_v2", "clients": "temp_result_v3"})
    chart_session("stale", {"sales": "temp_result_v4"})
    backdate_session("stale", 25 * 3600)

    assert collect_keep() == {"temp_result_v1", "temp_result_v2", "temp_result_v3"}


def test_collect_keep_tolerates_odd_session_states():
    SessionMemory("empty").set_table("sales")
    connection = sqlite3.connect(settings.sqlite_path)
    try:
        connection.executemany(
            "INSERT INTO session_state VALUES (?, ?, ?)",
            [
                ("broken", "{not json", time.time()),
                (
                    "legacy",
                    json.dumps({"last_chart": {"result_table": "temp_result_v7"}}),
                    time.time(),
                ),
                ("list", "[]", time.time()),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    assert collect_keep() == {"temp_result_v7"}


def test_cleanup_keeps_dashboard_tables_and_dashboard_still_opens():
    db = DuckDBManager()
    try:
        tile_table = db.save_step_result("raw", "SELECT 1 AS value")
        chart_table = db.save_step_result("raw", "SELECT 2 AS value")
        stale_table = db.save_step_result("raw", "SELECT 3 AS value")
        dashboard = make_dashboard(tile_table)
        store = DashboardStore()
        store.create(dashboard)
        chart_session("fresh", {"sales": chart_table})
        age_all(db, settings.result_ttl_s + 60)

        assert cleanup(db) == [stale_table]

        assert result_tables(db) == {tile_table, chart_table}
        assert store.get(dashboard.id) == dashboard
    finally:
        db.close()


def test_cleanup_logs_failures_instead_of_raising():
    db = DuckDBManager()
    db.close()
    assert cleanup(db) == []


def test_startup_cleanup_drops_stale_results():
    db = DuckDBManager()
    stale_table = db.save_step_result("raw", "SELECT 1 AS value")
    age_all(db, settings.result_ttl_s + 60)
    db.close()

    with TestClient(app):
        pass

    db = DuckDBManager()
    try:
        assert stale_table not in result_tables(db)
    finally:
        db.close()
