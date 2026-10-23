"""Реестр дашбордов — повторный доступ из UI и полный CRUD жизненный цикл.

Требование: созданный дашборд доступен повторно из чата и из
интерфейса платформы (см. core/dashboards/store.py).
"""

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.dashboards.store import DashboardStore, snapshot_records
from app.core.viz.vega_spec_builder import SPEC_ROW_LIMIT, build_vega_spec

router = APIRouter()


class DashboardCreate(BaseModel):
    id: str | None = None
    session_id: str
    title: str | None = "Новый дашборд"
    template_id: str | None = None
    tiles: list[DashboardTile] = Field(default_factory=list)


class DashboardUpdate(BaseModel):
    title: str | None = None
    tiles: list[DashboardTile] | None = None


@router.get("")
async def list_dashboards(session_id: str | None = Query(default=None)) -> list[dict]:
    """Список дашбордов (id, заголовок, число плиток, когда создан и изменён)."""
    return [summary.model_dump() for summary in DashboardStore().list(session_id)]


@router.get("/{dashboard_id}")
async def get_dashboard(dashboard_id: str) -> dict:
    """Полный дашборд: плитки с Vega-Lite спеками и снимками данных."""
    dashboard = DashboardStore().get(dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Дашборд не найден")
    return dashboard.model_dump()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_dashboard(payload: DashboardCreate) -> dict:
    """Создать новый дашборд."""
    raw_title = (payload.title or "").strip()
    title = raw_title if raw_title else "Новый дашборд"
    kwargs: dict[str, Any] = {
        "session_id": payload.session_id,
        "title": title,
        "template_id": payload.template_id,
        "tiles": payload.tiles,
    }
    if payload.id:
        kwargs["id"] = payload.id
    dashboard = Dashboard(**kwargs)
    store = DashboardStore()
    dashboard_id = store.create(dashboard)
    saved = store.get(dashboard_id)
    return saved.model_dump() if saved else dashboard.model_dump()


@router.put("/{dashboard_id}")
async def update_dashboard(dashboard_id: str, payload: DashboardUpdate) -> dict:
    """Обновить заголовок и/или плитки дашборда."""
    store = DashboardStore()
    updated = store.update(dashboard_id, title=payload.title, tiles=payload.tiles)
    if updated is None:
        raise HTTPException(status_code=404, detail="Дашборд не найден")
    return updated.model_dump()


@router.delete("/{dashboard_id}")
async def delete_dashboard(dashboard_id: str) -> dict:
    """Удалить дашборд со всеми связанными плитками."""
    store = DashboardStore()
    deleted = store.delete(dashboard_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Дашборд не найден")
    return {"ok": True, "id": dashboard_id}


@router.post("/{dashboard_id}/recalculate")
async def recalculate_dashboard(dashboard_id: str, request: Request) -> dict:
    """Пересчитать снимок данных для плиток дашборда."""
    store = DashboardStore()
    dashboard = store.get(dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Дашборд не найден")

    db = getattr(request.app.state, "db", None)
    now = time.time()
    for tile in dashboard.tiles:
        if tile.result_table and db is not None:
            try:
                if db.columns(tile.result_table):
                    records = db.to_records(tile.result_table, limit=SPEC_ROW_LIMIT)
                    tile.snapshot_data = snapshot_records(records)
                    tile.snapshot_at = now
                    if isinstance(tile.spec, dict):
                        usermeta = tile.spec.get("usermeta") or {}
                        if isinstance(usermeta, dict) and usermeta.get("columns"):
                            titles = {
                                col["field"]: col["title"]
                                for col in usermeta.get("columns", [])
                                if isinstance(col, dict) and col.get("field") and col.get("title")
                            }
                            tile.spec = build_vega_spec(
                                usermeta.get("chart_type")
                                or usermeta.get("requested_type")
                                or "bar",
                                result_table=tile.result_table,
                                data=records,
                                titles=titles,
                                title=tile.spec.get("title") or tile.title,
                            )
                        elif "data" in tile.spec and isinstance(tile.spec["data"], dict):
                            tile.spec["data"]["values"] = snapshot_records(records)
            except Exception:  # noqa: BLE001, S110
                pass
        elif tile.is_kpi and tile.snapshot_data is not None:
            tile.snapshot_at = now

    updated = store.update(dashboard_id, title=dashboard.title, tiles=dashboard.tiles)
    return updated.model_dump() if updated else dashboard.model_dump()
