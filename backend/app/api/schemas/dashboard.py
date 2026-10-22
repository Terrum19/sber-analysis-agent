"""Pydantic-схемы дашбордов (плитка = одна визуализация)."""

from pydantic import BaseModel, Field

from app.core.dashboards.models import DashboardSummary, DashboardTile

__all__ = [
    "DashboardCreate",
    "DashboardOut",
    "DashboardSummary",
    "DashboardTileOut",
    "DashboardUpdate",
]


class DashboardTileOut(DashboardTile):
    stale: bool = False


class DashboardOut(BaseModel):
    id: str
    session_id: str
    title: str
    template_id: str | None = None
    tiles: list[DashboardTileOut] = Field(default_factory=list)
    created_at: float
    updated_at: float


class DashboardCreate(BaseModel):
    session_id: str
    title: str
    tiles: list[DashboardTile] = Field(default_factory=list)


class DashboardUpdate(BaseModel):
    title: str | None = None
    tiles: list[DashboardTile] | None = None
