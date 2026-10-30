"""Схемы плана исследования: ResearchStep и ResearchPlan."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.dashboards.templates import TEMPLATES

# Ссылки на таблицы в плане: $prev — результат последнего transform-шага,
# $source — исходная таблица источника, $step_N — результат шага N.
PREVIOUS = "$prev"
SOURCE = "$source"

# Воспоминание сессии: длина записи (та же у API) и записей за один план.
# Пределы попадают в JSON-схему плана: свободная строка без них — место,
# где слабая модель зацикливается (#81).
NOTE_LIMIT = 500
NOTES_PER_PLAN = 3


class ResearchStep(BaseModel):
    """Один шаг плана исследования."""

    # input обязателен в схеме: без него модель молча продолжала с $prev,
    # хотя второй независимый расчёт нужен по исходной таблице.
    model_config = ConfigDict(
        json_schema_extra={"required": ["tool", "input", "arguments", "purpose"]}
    )

    step_id: int = 0
    # null в схеме не разрешаем: шаг без инструмента валидация всё равно отклонит.
    tool: str | None = Field(default=None, json_schema_extra={"anyOf": [{"type": "string"}]})
    input: str = PREVIOUS
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Старый позиционный формат: раскладывается по аргументам тула по порядку.
    columns: list[Any] = Field(default_factory=list)
    purpose: str = ""
    # P1: явные зависимости DAG. $prev/$step_N выводятся автоматически.
    depends_on: list[int] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_llm_output(cls, data: dict) -> dict:
        """Нормализует частые ошибки LLM при генерации ResearchStep.

        LLM часто присылает:
        - ``description`` вместо ``purpose``
        - ``properties`` (JSON-схему) вместо плоского списка ``columns``
        - вообще не присылает ``tool``
        """
        if not isinstance(data, dict):
            return data

        # purpose ← description (если purpose отсутствует или пуст)
        if not data.get("purpose") and data.get("description"):
            data["purpose"] = data.pop("description")

        # columns ← properties.columns (если LLM вложил схему в properties)
        if not data.get("columns") and isinstance(data.get("properties"), dict):
            props = data["properties"]
            cols = props.get("columns")
            if isinstance(cols, list):
                data["columns"] = [str(c) for c in cols]
            elif isinstance(cols, dict):
                # JSON-схема вида {"enum": [...], "type": "string", ...}
                enum_vals = cols.get("enum")
                if isinstance(enum_vals, list):
                    data["columns"] = [str(c) for c in enum_vals]
            data.pop("properties", None)

        # tool по умолчанию None, если не прислали
        data.setdefault("tool", None)

        # arguments ← args / params / parameters (синонимы, которые шлёт LLM)
        arguments = dict(data.get("arguments") or {})
        for key in ("args", "params", "parameters"):
            extra = data.pop(key, None)
            if isinstance(extra, dict):
                arguments = {**extra, **arguments}
        data["arguments"] = arguments

        # input: null/"" → $prev; число N → $step_N
        if data.get("input") in (None, ""):
            data.pop("input", None)
        elif isinstance(data["input"], int):
            data["input"] = f"$step_{data['input']}"

        # depends_on: одиночное число -> список; отрицательные id запрещены.
        depends = data.get("depends_on")
        if isinstance(depends, int):
            depends = [depends]
        if isinstance(depends, list):
            if any(isinstance(item, bool) or not isinstance(item, int) for item in depends):
                raise ValueError("depends_on должен содержать целые id шагов")
            if any(item < 0 for item in depends):
                raise ValueError("depends_on не может содержать отрицательные id")
            data["depends_on"] = depends

        return data


class DashboardTilePlan(BaseModel):
    title: str
    step_id: int
    chart_type: Literal[
        "bar", "line", "area", "scatter", "histogram", "box", "heatmap", "table", "kpi", "auto"
    ] = "auto"
    is_kpi: bool = False
    insight_needed: bool = False

    @field_validator("chart_type", "is_kpi", "insight_needed", mode="before")
    @classmethod
    def _null_is_default(cls, value: Any, info) -> Any:
        if value is None:
            return cls.model_fields[info.field_name].default
        return value.strip().lower() if isinstance(value, str) else value


class ResearchPlan(BaseModel):
    """Полный план исследования: упорядоченные шаги + мета."""

    model_config = ConfigDict(json_schema_extra={"required": ["steps"]})

    steps: list[ResearchStep] = Field(default_factory=list)
    final_viz: Literal[
        "bar",
        "line",
        "area",
        "scatter",
        "histogram",
        "box",
        "heatmap",
        "table",
        "kpi",
        "auto",
        "none",
    ] = "auto"
    dashboard_requested: bool = False
    # Шаг, чей результат идёт в ответ и на график (None — последний шаг).
    result_step: int | None = None
    dashboard_title: str | None = None
    template_id: str | None = Field(
        default=None,
        json_schema_extra={
            "anyOf": [{"type": "string", "enum": [t.id for t in TEMPLATES]}, {"type": "null"}]
        },
    )
    dashboard_tiles: list[DashboardTilePlan] = Field(default_factory=list)
    # Что пользователь попросил запомнить в этом вопросе (воспоминания сессии, #197).
    remember: list[Annotated[str, Field(max_length=NOTE_LIMIT)]] = Field(
        default_factory=list, max_length=NOTES_PER_PLAN
    )

    @field_validator(
        "final_viz", "dashboard_requested", "dashboard_tiles", "remember", mode="before"
    )
    @classmethod
    def _null_is_default(cls, value: Any, info) -> Any:
        """LLM присылает null вместо значения по умолчанию: final_viz=null -> auto."""
        if value is None:
            field_info = cls.model_fields[info.field_name]
            return field_info.get_default(call_default_factory=True)
        return value

    @field_validator("remember", mode="before")
    @classmethod
    def _one_note_is_list(cls, value: Any) -> Any:
        """Одна запись строкой вместо списка; лишние записи и хвост длинной — отрезаются.

        Провайдер может не соблюдать пределы схемы, а план из-за записи терять нельзя.
        """
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return value
        return [
            note[:NOTE_LIMIT] if isinstance(note, str) else note for note in value[:NOTES_PER_PLAN]
        ]

    @model_validator(mode="after")
    def _tiles_mean_dashboard(self) -> "ResearchPlan":
        if self.dashboard_tiles:
            self.dashboard_requested = True
        return self

    @field_validator("final_viz", mode="before")
    @classmethod
    def normalize_final_viz(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value
