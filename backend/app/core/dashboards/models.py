"""Модели дашборда: плитка = одна визуализация + метаданные.

Хранит и Vega-конфиг, и SQL, построивший плитку — чтобы дашборд был
воспроизводим и доступен повторно (см. core/memory/artifact_registry.py).
"""

from typing import Any

from pydantic import BaseModel, Field


class DashboardTile(BaseModel):
    """Одна плитка дашборда."""

    title: str
    spec: dict[str, Any]
    sql: str | None = None
    layout: dict[str, int] = Field(default_factory=lambda: {"x": 0, "y": 0, "w": 6, "h": 4})


class Dashboard(BaseModel):
    """Дашборд: набор плиток, привязанный к сессии."""

    title: str
    tiles: list[DashboardTile] = Field(default_factory=list)
