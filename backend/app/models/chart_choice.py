"""Выбор графика агентом по готовым результатам: вид и колонки осей.

Общая часть плитки дашборда (ChartChoice) и графика ответа в чате
(AnswerChart): какой вид, какие колонки на x, y и в цвете, сколько
строк показать.
"""

from typing import Any, Literal

from pydantic import BaseModel, field_validator

ChartType = Literal["bar", "line", "area", "scatter", "histogram", "box", "heatmap", "table", "kpi"]


class ChartPick(BaseModel):
    """Вид графика и колонки результата; limit — топ-N строк по y (для bar)."""

    chart_type: ChartType = "table"
    x: str | None = None
    y: str | None = None
    color: str | None = None
    limit: int | None = None
    reason: str = ""

    @field_validator("chart_type", mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        text = value.strip().lower() if isinstance(value, str) else value
        return text if text in ChartType.__args__ else "table"

    @field_validator("limit", mode="before")
    @classmethod
    def _positive_limit(cls, value: Any) -> Any:
        """Модель шлёт 0, "10" или мусор вместо числа: без лимита, а не ошибка схемы."""
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None


class AnswerChart(ChartPick):
    """График ответа в чате: таблицу какого шага показать и как; без step_id — нет выбора.

    Все поля обязательны (null — явное «нет»): в json_schema поле со значением
    по умолчанию необязательно, и модель отвечала одним {"step_id": 1} —
    вид по умолчанию table выдавался за её выбор.
    """

    step_id: int | None
    chart_type: ChartType
    x: str | None
    y: str | None
    color: str | None
    limit: int | None
