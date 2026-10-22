import time
from dataclasses import dataclass, field

from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.dashboards.store import DashboardStore, snapshot_records
from app.core.data.duckdb_manager import DuckDBManager
from app.core.viz.vega_spec_builder import SPEC_ROW_LIMIT, build_vega_spec


@dataclass
class Recalculated:
    dashboard: Dashboard
    stale_tiles: set[str] = field(default_factory=set)


def rebuild_spec(tile: DashboardTile, records: list[dict]) -> dict:
    usermeta = tile.spec.get("usermeta") or {}
    titles = {
        column["field"]: column["title"]
        for column in usermeta.get("columns") or []
        if column.get("field") and column.get("title")
    }
    title = tile.spec.get("title")
    return build_vega_spec(
        usermeta.get("chart_type") or "bar",
        result_table=tile.result_table,
        data=records,
        titles=titles,
        title=title if isinstance(title, str) else None,
    )


def recalculate(
    dashboard_id: str, db: DuckDBManager, store: DashboardStore | None = None
) -> Recalculated | None:
    store = store or DashboardStore()
    dashboard = store.get(dashboard_id)
    if dashboard is None:
        return None
    stale_tiles = set()
    for tile in dashboard.tiles:
        if not tile.result_table:
            continue
        if not db.columns(tile.result_table):
            stale_tiles.add(tile.id)
            continue
        records = db.to_records(tile.result_table, limit=SPEC_ROW_LIMIT)
        tile.spec = rebuild_spec(tile, records)
        tile.snapshot_data = snapshot_records(records)
        tile.snapshot_at = time.time()
    updated = store.update(dashboard_id, tiles=dashboard.tiles)
    if updated is None:
        return None
    return Recalculated(dashboard=updated, stale_tiles=stale_tiles)
