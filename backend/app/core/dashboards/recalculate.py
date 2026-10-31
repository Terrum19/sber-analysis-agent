import time
from dataclasses import dataclass, field

from app.core.dashboards.models import Dashboard
from app.core.dashboards.store import DashboardStore, snapshot_records
from app.core.data.duckdb_manager import DuckDBManager
from app.core.viz.vega_spec_builder import (
    SPEC_ROW_LIMIT,
    build_vega_spec,
    detect_wide_matrix,
    infer_field_types,
)


@dataclass
class Recalculated:
    dashboard: Dashboard
    stale_tiles: set[str] = field(default_factory=set)


def rebuild_spec(spec: dict, result_table: str | None, records: list[dict]) -> dict:
    """Та же спека (тип графика, подписи, заголовок) по новым строкам результата."""
    usermeta = spec.get("usermeta")
    if not usermeta:
        return {**spec, "data": {"values": snapshot_records(records)}}
    titles = {
        column["field"]: column["title"]
        for column in usermeta.get("columns") or []
        if column.get("field") and column.get("title")
    }
    title = spec.get("title")
    target_type = usermeta.get("requested_type") or usermeta.get("chart_type") or "bar"
    is_matrix = bool(records and detect_wide_matrix(records, infer_field_types(records)))
    x = None if is_matrix else usermeta.get("x")
    y = None if is_matrix else usermeta.get("y")
    color = None if is_matrix else usermeta.get("color")
    return build_vega_spec(
        target_type,
        result_table=result_table,
        data=records,
        titles=titles,
        title=title if isinstance(title, str) else None,
        x=x,
        y=y,
        color=color,
    )


def respec(spec: dict, chart_type: str) -> dict:
    """Спека в другом виде по её же строкам (data.values): смена вида до сохранения дашборда.

    Без usermeta (спека не из build_vega_spec) перестраивать не по чему — как есть.
    """
    usermeta = spec.get("usermeta")
    if not usermeta:
        return spec
    data = spec.get("data")
    records = data.get("values") if isinstance(data, dict) else None
    return rebuild_spec(
        {**spec, "usermeta": {**usermeta, "requested_type": chart_type}},
        usermeta.get("result_table"),
        records if isinstance(records, list) else [],
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
        if tile.hidden or not tile.result_table:
            continue
        # Под одним lock: очистка temp_result_vN не удалит таблицу между проверкой и чтением.
        with db.lock:
            if not db.columns(tile.result_table):
                stale_tiles.add(tile.id)
                continue
            records = db.to_records(tile.result_table, limit=SPEC_ROW_LIMIT)
        tile.spec = rebuild_spec(tile.spec, tile.result_table, records)
        tile.snapshot_data = snapshot_records(records)
        tile.snapshot_at = time.time()
    updated = store.update(dashboard_id, tiles=dashboard.tiles)
    if updated is None:
        return None
    return Recalculated(dashboard=updated, stale_tiles=stale_tiles)
