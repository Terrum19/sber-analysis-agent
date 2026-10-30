"""Семантический слой: метрики, измерения и связи таблиц.

Модель описывает бизнес-смысл таблицы поверх физических колонок:
«выручка» = SUM(revenue), «по месяцам» = date_trunc('month', order_date).
Планировщик получает управляемые метрики вместо догадок по именам колонок.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SemanticMetric(BaseModel):
    """Бизнес-метрика: агрегат колонки или проверенный SQL-выражение."""

    name: str
    title: str
    description: str = ""
    aggregation: Literal["SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX", "MEDIAN"]
    column: str | None = None
    sql: str | None = None
    format: Literal["number", "currency", "percent", "duration"] = "number"
    synonyms: list[str] = Field(default_factory=list)

    @field_validator("name", "title")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("значение не может быть пустым")
        return value.strip()


class SemanticDimension(BaseModel):
    """Измерение: категория, дата или идентификатор."""

    name: str
    column: str
    kind: Literal["categorical", "temporal", "identifier"]
    synonyms: list[str] = Field(default_factory=list)
    time_grains: list[Literal["day", "week", "month", "quarter", "year"]] = Field(
        default_factory=list
    )


class SemanticJoin(BaseModel):
    """Разрешённая связь двух таблиц (произвольные join-условия запрещены)."""

    left_table: str
    right_table: str
    left_column: str
    right_column: str
    relationship: Literal["many_to_one", "one_to_many", "one_to_one"]


class SemanticModel(BaseModel):
    """Semantic model одной таблицы."""

    table: str
    description: str = ""
    metrics: list[SemanticMetric] = Field(default_factory=list)
    dimensions: list[SemanticDimension] = Field(default_factory=list)
    joins: list[SemanticJoin] = Field(default_factory=list)
    default_time_dimension: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    version: int = 1
