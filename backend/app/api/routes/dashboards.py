"""Реестр дашбордов — повторный доступ и правка из UI.

Требование: созданный дашборд доступен повторно из чата и из
интерфейса платформы (см. core/dashboards/store.py).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import get_db
from app.api.schemas.dashboard import (
    DashboardCreate,
    DashboardOut,
    DashboardSummary,
    DashboardTileOut,
    DashboardUpdate,
)
from app.core.dashboards.models import Dashboard
from app.core.dashboards.recalculate import recalculate
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager

router = APIRouter()

NOT_FOUND = "Дашборд не найден"


def dashboard_out(dashboard: Dashboard | None, stale_tiles: set[str] | None = None) -> DashboardOut:
    if dashboard is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return DashboardOut(
        **dashboard.model_dump(exclude={"tiles"}),
        tiles=[
            DashboardTileOut(**tile.model_dump(), stale=tile.id in (stale_tiles or set()))
            for tile in dashboard.tiles
        ],
    )


@router.get("", response_model=list[DashboardSummary])
def list_dashboards(session_id: str | None = None) -> list[DashboardSummary]:
    """Список дашбордов (id, заголовок, число плиток, время); session_id необязателен."""
    return DashboardStore().list(session_id)


@router.get("/{dashboard_id}", response_model=DashboardOut)
def get_dashboard(dashboard_id: str) -> DashboardOut:
    """Полный дашборд: плитки с Vega-Lite спеками и снимками данных."""
    return dashboard_out(DashboardStore().get(dashboard_id))


@router.post("", response_model=DashboardOut, status_code=status.HTTP_201_CREATED)
def create_dashboard(payload: DashboardCreate) -> DashboardOut:
    dashboard = Dashboard(session_id=payload.session_id, title=payload.title, tiles=payload.tiles)
    DashboardStore().create(dashboard)
    return dashboard_out(dashboard)


@router.put("/{dashboard_id}", response_model=DashboardOut)
def update_dashboard(dashboard_id: str, payload: DashboardUpdate) -> DashboardOut:
    return dashboard_out(
        DashboardStore().update(dashboard_id, title=payload.title, tiles=payload.tiles)
    )


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dashboard(dashboard_id: str) -> Response:
    if not DashboardStore().delete(dashboard_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{dashboard_id}/recalculate", response_model=DashboardOut)
def recalculate_dashboard(
    dashboard_id: str, db: Annotated[DuckDBManager, Depends(get_db)]
) -> DashboardOut:
    result = recalculate(dashboard_id, db)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return dashboard_out(result.dashboard, result.stale_tiles)
