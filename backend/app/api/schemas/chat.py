"""Pydantic-схемы API диалога (контракт фронтенд <-> бэкенд)."""
from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    datasource_id: str | None = None
    title: str | None = None


class SessionOut(BaseModel):
    session_id: str
    datasource_id: str | None = None
    title: str | None = None


class ChatRequest(BaseModel):
    """Один ход диалога."""
    session_id: str
    question: str


class ChartPayload(BaseModel):
    """Готовая визуализация (Vega-Lite спека + краткое пояснение)."""
    spec: dict[str, Any]
    caption: str


class DashboardPayload(BaseModel):
    dashboard_id: str
    title: str
    tiles: list[dict[str, Any]] = Field(default_factory=list)


class ChatEvent(BaseModel):
    """Событие стрима для фронтенда."""
    type: Literal["text", "chart", "dashboard", "error", "done"]
    data: dict[str, Any] = Field(default_factory=dict)


class ExportRequest(BaseModel):
    session_id: str
    format: Literal["xlsx", "pdf"] = "xlsx"
    result_ref: str | None = None  # ссылка на temp_result_vN в DuckDB
