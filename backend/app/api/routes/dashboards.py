"""Реестр дашбордов — повторный доступ и правка из UI.

Требование: созданный дашборд доступен повторно из чата и из
интерфейса платформы (см. core/dashboards/store.py).
"""

from collections.abc import Collection
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse

from app.api.deps import get_db
from app.api.schemas.dashboard import (
    DashboardCreate,
    DashboardOut,
    DashboardSummary,
    DashboardTileOut,
    DashboardUpdate,
)
from app.config import settings
from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.dashboards.recalculate import recalculate
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager
from app.core.export.excel import ExcelExporter

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


def own_tiles(tiles: list[DashboardTile], keep: Collection[str] = ()) -> list[DashboardTile]:
    """Плитки с id из keep сохраняют его (первая с таким id), остальные получают новый.

    dashboard_tiles.id — глобальный ключ: плитка с id из другого дашборда
    или повтор id в одном запросе дали бы IntegrityError.
    """
    seen: set[str] = set()
    own = []
    for tile in tiles:
        if tile.id in keep and tile.id not in seen:
            seen.add(tile.id)
            own.append(DashboardTile(**tile.model_dump()))
        else:
            own.append(DashboardTile(**tile.model_dump(exclude={"id"}), id=uuid4().hex[:12]))
    return own


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
    dashboard = Dashboard(
        session_id=payload.session_id, title=payload.title, tiles=own_tiles(payload.tiles)
    )
    DashboardStore().create(dashboard)
    return dashboard_out(dashboard)


@router.put("/{dashboard_id}", response_model=DashboardOut)
def update_dashboard(dashboard_id: str, payload: DashboardUpdate) -> DashboardOut:
    store = DashboardStore()
    current = store.get(dashboard_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    tiles = payload.tiles
    if tiles is not None:
        tiles = own_tiles(tiles, keep={tile.id for tile in current.tiles})
    return dashboard_out(store.update(dashboard_id, title=payload.title, tiles=tiles))


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


@router.get("/{dashboard_id}/export")
def export_dashboard(
    dashboard_id: str,
    db: Annotated[DuckDBManager, Depends(get_db)],
    format: Literal["xlsx", "pdf"] = "xlsx",
) -> FileResponse:
    """Экспорт дашборда в .xlsx (или .pdf)."""
    if format == "pdf":
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "Экспорт дашборда в PDF пока не реализован. Попробуйте .xlsx.",
        )
    dashboard = DashboardStore().get(dashboard_id)
    if dashboard is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)

    filename = f"dashboard_{dashboard_id}_{uuid4().hex[:8]}.xlsx"
    export_dir = Path(settings.storage_dir) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / filename
    ExcelExporter().export_dashboard(dashboard, str(path), db=db)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
