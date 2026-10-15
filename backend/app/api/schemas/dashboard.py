"""Pydantic-схемы дашбордов (плитка = одна визуализация)."""

from typing import Any

from pydantic import BaseModel, Field


class DashboardTile(BaseModel):
    title: str
    spec: dict[str, Any]  # Vega-Lite / ECharts конфиг
    sql: str | None = None  # запрос, построивший плитку
    layout: dict[str, int] = Field(default_factory=dict)  # x, y, w, h


class DashboardConfig(BaseModel):
    dashboard_id: str
    session_id: str
    title: str
    tiles: list[DashboardTile] = Field(default_factory=list)
