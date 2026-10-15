"""Реестр дашбордов — повторный доступ из UI.

Требование: созданный дашборд доступен повторно из чата и из
интерфейса платформы (см. core/memory/artifact_registry.py).
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def list_dashboards(session_id: str) -> list[dict]:
    """Список дашбордов сессии (id, заголовок, создан когда)."""
    # TODO(round-2): ArtifactRegistry.list(session_id)
    return []


@router.get("/{dashboard_id}")
async def get_dashboard(dashboard_id: str) -> dict:
    """Полный конфиг дашборда: плитки + Vega-Lite спеки + SQL."""
    # TODO(round-2): ArtifactRegistry.get(dashboard_id)
    return {"id": dashboard_id, "tiles": []}
