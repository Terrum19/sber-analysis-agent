"""Pydantic-схемы API диалога (контракт фронтенд <-> бэкенд)."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.api.schemas.dashboard import DashboardTileOut
from app.core.dashboards.models import Dashboard
from app.models.dashboard_plan import DashboardIdea
from app.models.research_step import NOTE_LIMIT


class SessionCreate(BaseModel):
    table: str | None = None
    datasource_id: str | None = None
    title: str | None = None


SessionTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class SessionUpdate(BaseModel):
    """Тело PATCH /sessions/{id}: новая таблица по умолчанию и/или новое название.

    Оба поля необязательны, но хотя бы одно должно быть задано (иначе 422).
    Тело ``{"table": ...}`` прежнего контракта остаётся валидным.
    """

    table: str | None = None
    title: SessionTitle | None = None

    @model_validator(mode="after")
    def require_field(self) -> Self:
        if self.table is None and self.title is None:
            raise ValueError("Нужно указать table или title.")
        return self


class SessionOut(BaseModel):
    session_id: str
    table: str | None = None
    datasource_id: str | None = None
    title: str | None = None
    created_at: datetime | None = None


MemoryText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NOTE_LIMIT)
]


class MemoryCreate(BaseModel):
    """Тело POST /sessions/{id}/memories: текст воспоминания."""

    text: MemoryText


class MemoryUpdate(BaseModel):
    """Тело PATCH /sessions/{id}/memories/{memory_id}: включить или выключить запись."""

    enabled: bool


class MemoryOut(BaseModel):
    """Воспоминание сессии (#197): выключенное не идёт в контекст планировщика и инсайта."""

    memory_id: str
    text: str
    source: str
    created_at: datetime
    enabled: bool
    # Ход, в котором запись сделана из чата; у добавленной через API — None.
    turn_id: str | None = None


class ChatRequest(BaseModel):
    """Один ход диалога."""

    session_id: str
    question: str
    recovery_response: str | None = None
    # turn_id прошлого хода из события done: вопрос продолжает именно его.
    reply_to: str | None = None
    # dashboard — ход строит дашборд по кнопке (#224); idea — выбранный вариант.
    mode: Literal["chat", "dashboard"] = "chat"
    idea: DashboardIdea | None = None


class ChartPayload(BaseModel):
    """Готовая визуализация (Vega-Lite спека + краткое пояснение)."""

    spec: dict[str, Any]
    caption: str


class DashboardPayload(BaseModel):
    dashboard_id: str
    title: str
    tiles: list[DashboardTileOut] = Field(default_factory=list)

    @classmethod
    def from_dashboard(cls, dashboard: Dashboard) -> Self:
        return cls(
            dashboard_id=dashboard.id,
            title=dashboard.title,
            tiles=[
                DashboardTileOut(**tile.model_dump(exclude={"snapshot_data"}))
                for tile in dashboard.tiles
            ],
        )


class ProgressPayload(BaseModel):
    """Прогресс хода: этап, его статус и шаг плана.

    ``fixing`` — шаг упал, планировщик исправляет его по тексту ошибки
    (``attempt`` — номер попытки исправления, ``error`` — текст ошибки).
    ``tables`` — на этапе planning: таблица хода и связанные с ней таблицы,
    которые видит планировщик (первая — $source).
    """

    stage: Literal["planning", "step", "insight", "chart", "dashboard", "outline", "tile"]
    status: Literal["started", "finished", "failed", "fixing"]
    step_id: int | None = None
    tool: str | None = None
    purpose: str | None = None
    total_steps: int | None = None
    elapsed_ms: int | None = None
    attempt: int | None = None
    error: str | None = None
    tables: list[str] | None = None
    # Плитка дашборда по кнопке (#224), к которой относится этап или шаг.
    tile_id: int | None = None


class DashboardIdeasRequest(BaseModel):
    """Тело POST /sessions/{id}/dashboard-ideas (необязательное): прежние варианты.

    Пусто — обычные варианты по таблице; названия в exclude — новые варианты,
    которые их не повторяют (пользователь перебрал все прежние).
    """

    exclude: list[Annotated[str, StringConstraints(max_length=200)]] = Field(
        default_factory=list, max_length=100
    )


class DashboardIdeasOut(BaseModel):
    """Ответ POST /sessions/{id}/dashboard-ideas: варианты дашборда для кнопок."""

    ideas: list[DashboardIdea]


class ChatEvent(BaseModel):
    """Событие стрима для фронтенда."""

    type: Literal[
        "text",
        "chart",
        "dashboard",
        "error",
        "done",
        "clarification_needed",
        "progress",
        "dashboard_outline",
        "dashboard_fallback",
        "table",
    ]
    data: dict[str, Any] = Field(default_factory=dict)


class ExportRequest(BaseModel):
    session_id: str
    format: Literal["xlsx", "pdf"] = "xlsx"
    result_ref: str | None = None  # ссылка на temp_result_vN в DuckDB
    dashboard_id: str | None = Field(default=None, max_length=64)
    question: str | None = Field(default=None, max_length=5000)
    headline: str | None = Field(default=None, max_length=3000)
    title: str | None = Field(default=None, max_length=500)
    session_title: str | None = Field(default=None, max_length=500)
    database_name: str | None = Field(default=None, max_length=500)
    source_name: str | None = Field(default=None, max_length=500)
    details: list[str] | None = Field(default=None, max_length=100)
    spec: dict[str, Any] | None = None
