"""Pydantic-схемы API диалога (контракт фронтенд <-> бэкенд)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    table: str
    datasource_id: str | None = None
    title: str | None = None


class SessionTable(BaseModel):
    table: str


class SessionOut(BaseModel):
    session_id: str
    table: str | None = None
    datasource_id: str | None = None
    title: str | None = None
    created_at: datetime | None = None


class ChatRequest(BaseModel):
    """Один ход диалога."""

    session_id: str
    question: str
    recovery_response: str | None = None


class ChartPayload(BaseModel):
    """Готовая визуализация (Vega-Lite спека + краткое пояснение)."""

    spec: dict[str, Any]
    caption: str


class DashboardPayload(BaseModel):
    dashboard_id: str
    title: str
    tiles: list[dict[str, Any]] = Field(default_factory=list)


class ProgressPayload(BaseModel):
    """Прогресс хода: этап, его статус и шаг плана.

    ``fixing`` — шаг упал, планировщик исправляет его по тексту ошибки
    (``attempt`` — номер попытки исправления, ``error`` — текст ошибки).
    """

    stage: Literal["planning", "step", "insight", "chart", "dashboard"]
    status: Literal["started", "finished", "failed", "fixing"]
    step_id: int | None = None
    tool: str | None = None
    purpose: str | None = None
    total_steps: int | None = None
    elapsed_ms: int | None = None
    attempt: int | None = None
    error: str | None = None


class ChatEvent(BaseModel):
    """Событие стрима для фронтенда."""

    type: Literal["text", "chart", "dashboard", "error", "done", "clarification_needed", "progress"]
    data: dict[str, Any] = Field(default_factory=dict)


class ExportRequest(BaseModel):
    session_id: str
    format: Literal["xlsx", "pdf"] = "xlsx"
    result_ref: str | None = None  # ссылка на temp_result_vN в DuckDB
    question: str | None = None
    headline: str | None = None
    details: list[str] | None = None
    spec: dict[str, Any] | None = None
