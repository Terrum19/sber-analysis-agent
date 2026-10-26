"""Схемы плана исследования: ResearchStep и ResearchPlan."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Ссылки на таблицы в плане: $prev — результат последнего transform-шага,
# $source — исходная таблица источника, $step_N — результат шага N.
PREVIOUS = "$prev"
SOURCE = "$source"


class ResearchStep(BaseModel):
    """Один шаг плана исследования."""

    step_id: int = 0
    tool: str | None = None
    input: str = PREVIOUS
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Старый позиционный формат: раскладывается по аргументам тула по порядку.
    columns: list[Any] = Field(default_factory=list)
    purpose: str = ""

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
    template_id: str | None = None
    dashboard_tiles: list[DashboardTilePlan] = Field(default_factory=list)

    @field_validator("final_viz", "dashboard_requested", "dashboard_tiles", mode="before")
    @classmethod
    def _null_is_default(cls, value: Any, info) -> Any:
        """LLM присылает null вместо значения по умолчанию: final_viz=null -> auto."""
        if value is None:
            field_info = cls.model_fields[info.field_name]
            return field_info.get_default(call_default_factory=True)
        return value

    @model_validator(mode="after")
    def _tiles_mean_dashboard(self) -> "ResearchPlan":
        if self.dashboard_tiles:
            self.dashboard_requested = True
        return self

    @field_validator("final_viz", mode="before")
    @classmethod
    def normalize_final_viz(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value
