"""Pydantic-схемы дашбордов (плитка = одна визуализация)."""

from pydantic import BaseModel, Field

from app.core.dashboards.models import DashboardSummary, DashboardTile

__all__ = [
    "DashboardCreate",
    "DashboardOut",
    "DashboardSummary",
    "DashboardTileIn",
    "DashboardTileOut",
    "DashboardUpdate",
]


class DashboardTileIn(DashboardTile):
    # result_table попадает в SQL как имя таблицы — пускаем только temp_result_vN.
    result_table: str | None = Field(default=None, pattern=r"^temp_result_v\d+$")


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
    tiles: list[DashboardTileIn] = Field(default_factory=list)


class DashboardUpdate(BaseModel):
    title: str | None = None
    tiles: list[DashboardTileIn] | None = None
