"""Реестр дашбордов — повторный доступ из UI.

Требование: созданный дашборд доступен повторно из чата и из
интерфейса платформы (см. core/memory/artifact_registry.py).
"""

from fastapi import APIRouter

from app.core.memory.artifact_registry import ArtifactRegistry

router = APIRouter()


@router.get("")
async def list_dashboards(session_id: str) -> list[dict]:
    """Список дашбордов сессии (id, заголовок, создан когда)."""
    return ArtifactRegistry().list(session_id)


@router.get("/{dashboard_id}")
async def get_dashboard(dashboard_id: str) -> dict:
    """Полный конфиг дашборда: плитки + Vega-Lite спеки + SQL."""
    return ArtifactRegistry().get(dashboard_id)
