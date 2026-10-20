"""Реестр дашбордов — повторный доступ из UI.

Требование: созданный дашборд доступен повторно из чата и из
интерфейса платформы (см. core/dashboards/store.py).
"""

from fastapi import APIRouter

from app.core.dashboards.store import DashboardStore

router = APIRouter()


@router.get("")
async def list_dashboards(session_id: str) -> list[dict]:
    """Список дашбордов сессии (id, заголовок, число плиток, когда создан и изменён)."""
    return [summary.model_dump() for summary in DashboardStore().list(session_id)]


@router.get("/{dashboard_id}")
async def get_dashboard(dashboard_id: str) -> dict:
    """Полный дашборд: плитки с Vega-Lite спеками и снимками данных."""
    dashboard = DashboardStore().get(dashboard_id)
    return dashboard.model_dump() if dashboard else {}
