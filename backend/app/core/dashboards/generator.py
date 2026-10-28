"""Генератор дашбордов по запросу.

Сценарий: «Создай дашборд/панель с ежеквартальной статистикой...».
Собирает плитки в дашборд, снимает данные их temp-таблиц, сохраняет
в DashboardStore и возвращает дашборд для повторного доступа из чата и UI.
"""

import time

from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.dashboards.store import DashboardStore, snapshot_records
from app.core.data.duckdb_manager import DuckDBManager
from app.core.viz.vega_spec_builder import SPEC_ROW_LIMIT


class DashboardGenerator:
    """Сборка и сохранение дашборда."""

    def __init__(
        self, db: DuckDBManager | None = None, store: DashboardStore | None = None
    ) -> None:
        self.db = db
        self.store = store or DashboardStore()

    async def generate(
        self,
        session_id: str,
        title: str,
        tiles: list[DashboardTile],
        template_id: str | None = None,
    ) -> Dashboard:
        """Собрать дашборд из плиток, снять данные плиток с result_table, сохранить."""
        for tile in tiles:
            if tile.result_table and self.db is not None:
                records = self.db.to_records(tile.result_table, limit=SPEC_ROW_LIMIT)
                tile.snapshot_data = snapshot_records(records)
                tile.snapshot_at = time.time()
        dashboard = Dashboard(
            session_id=session_id, title=title, template_id=template_id, tiles=tiles
        )
        self.store.create(dashboard)
        return dashboard
