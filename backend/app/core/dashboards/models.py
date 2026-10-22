"""Модели дашборда: плитка = одна визуализация + метаданные.

Плитка хранит Vega-спеку, ссылку на шаг и temp-таблицу, построившие её,
и снимок данных — чтобы дашборд был воспроизводим и доступен повторно
(см. core/dashboards/store.py).
"""

import time
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class DashboardTile(BaseModel):
    """Одна плитка дашборда."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    title: str = "Визуализация"
    spec: dict[str, Any] = Field(default_factory=dict)
    source_step_id: int | None = None
    result_table: str | None = None
    insight: str | None = None
    is_kpi: bool = False
    layout: dict[str, Any] = Field(default_factory=lambda: {"x": 0, "y": 0, "w": 6, "h": 4})
    snapshot_data: Any = None
    snapshot_at: float | None = None


class Dashboard(BaseModel):
    """Дашборд: набор плиток, привязанный к сессии."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    session_id: str
    title: str
    template_id: str | None = None
    tiles: list[DashboardTile] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class DashboardSummary(BaseModel):
    id: str
    session_id: str
    title: str
    tiles_count: int
    created_at: float
    updated_at: float
