from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import settings
from app.core.dashboards.models import Dashboard, DashboardSummary, DashboardTile
from app.core.viz.vega_spec_builder import to_json_value

SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboards (
  id TEXT PRIMARY KEY, session_id TEXT, title TEXT,
  template_id TEXT, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS dashboard_tiles (
  id TEXT PRIMARY KEY, dashboard_id TEXT REFERENCES dashboards(id) ON DELETE CASCADE,
  order_index INTEGER, title TEXT, source_step_id INTEGER, result_table TEXT,
  spec_json TEXT, insight TEXT, is_kpi INTEGER,
  layout_json TEXT, snapshot_json TEXT, snapshot_at REAL);
CREATE INDEX IF NOT EXISTS ix_dashboards_session ON dashboards(session_id);
"""


def snapshot_records(records: list[dict]) -> list[dict]:
    return [{key: to_json_value(value) for key, value in record.items()} for record in records]


class DashboardStore:
    def __init__(self, path: str | None = None) -> None:
        self._db_path = Path(path or settings.sqlite_path)

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, dashboard: Dashboard) -> str:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO dashboards (id, session_id, title, template_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    dashboard.id,
                    dashboard.session_id,
                    dashboard.title,
                    dashboard.template_id,
                    dashboard.created_at,
                    dashboard.updated_at,
                ),
            )
            self._insert_tiles(connection, dashboard.id, dashboard.tiles)
        return dashboard.id

    def get(self, dashboard_id: str) -> Dashboard | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM dashboards WHERE id = ?", (dashboard_id,)
            ).fetchone()
            if row is None:
                return None
            tile_rows = connection.execute(
                "SELECT * FROM dashboard_tiles WHERE dashboard_id = ? ORDER BY order_index",
                (dashboard_id,),
            ).fetchall()
        return Dashboard(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            template_id=row["template_id"],
            tiles=[self._tile(tile_row) for tile_row in tile_rows],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list(self, session_id: str | None = None) -> list[DashboardSummary]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT dashboards.*, COUNT(dashboard_tiles.id) AS tiles_count"
                " FROM dashboards"
                " LEFT JOIN dashboard_tiles ON dashboard_tiles.dashboard_id = dashboards.id"
                " WHERE ? IS NULL OR dashboards.session_id = ?"
                " GROUP BY dashboards.id"
                " ORDER BY dashboards.updated_at DESC",
                (session_id, session_id),
            ).fetchall()
        return [
            DashboardSummary(
                id=row["id"],
                session_id=row["session_id"],
                title=row["title"],
                tiles_count=row["tiles_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def update(
        self,
        dashboard_id: str,
        title: str | None = None,
        tiles: list[DashboardTile] | None = None,
    ) -> Dashboard | None:
        with self._transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM dashboards WHERE id = ?", (dashboard_id,)
            ).fetchone()
            if exists is None:
                return None
            if title is not None:
                connection.execute(
                    "UPDATE dashboards SET title = ? WHERE id = ?", (title, dashboard_id)
                )
            if tiles is not None:
                connection.execute(
                    "DELETE FROM dashboard_tiles WHERE dashboard_id = ?", (dashboard_id,)
                )
                self._insert_tiles(connection, dashboard_id, tiles)
            connection.execute(
                "UPDATE dashboards SET updated_at = ? WHERE id = ?", (time.time(), dashboard_id)
            )
        return self.get(dashboard_id)

    def delete(self, dashboard_id: str) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute("DELETE FROM dashboards WHERE id = ?", (dashboard_id,))
        return cursor.rowcount > 0

    def update_snapshot(self, tile_id: str, records: list[dict]) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE dashboard_tiles SET snapshot_json = ?, snapshot_at = ? WHERE id = ?",
                (
                    json.dumps(snapshot_records(records), ensure_ascii=False),
                    time.time(),
                    tile_id,
                ),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _insert_tiles(
        connection: sqlite3.Connection, dashboard_id: str, tiles: list[DashboardTile]
    ) -> None:
        connection.executemany(
            "INSERT INTO dashboard_tiles (id, dashboard_id, order_index, title, source_step_id,"
            " result_table, spec_json, insight, is_kpi, layout_json, snapshot_json, snapshot_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    tile.id,
                    dashboard_id,
                    order_index,
                    tile.title,
                    tile.source_step_id,
                    tile.result_table,
                    json.dumps(tile.spec, ensure_ascii=False),
                    tile.insight,
                    int(tile.is_kpi),
                    json.dumps(tile.layout),
                    None
                    if tile.snapshot_data is None
                    else json.dumps(snapshot_records(tile.snapshot_data), ensure_ascii=False),
                    tile.snapshot_at,
                )
                for order_index, tile in enumerate(tiles)
            ],
        )

    @staticmethod
    def _tile(row: sqlite3.Row) -> DashboardTile:
        return DashboardTile(
            id=row["id"],
            title=row["title"],
            spec=json.loads(row["spec_json"]),
            source_step_id=row["source_step_id"],
            result_table=row["result_table"],
            insight=row["insight"],
            is_kpi=bool(row["is_kpi"]),
            layout=json.loads(row["layout_json"]),
            snapshot_data=None
            if row["snapshot_json"] is None
            else json.loads(row["snapshot_json"]),
            snapshot_at=row["snapshot_at"],
        )
