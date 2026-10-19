"""TS-тул: экстраполяция/прогноз по линейному тренду.

Пример сценария: «Какой рост X ожидается в ближайшие кварталы при
текущих условиях?».
"""

from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import Field, model_validator

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier
from app.core.tools.sql.atomic import Interval
from app.core.tools.stats._common import rows_result

_MONTHS = {"month": 1, "quarter": 3, "year": 12}
_DAYS = {"day": 1, "week": 7}


def shift(value: date, interval: str, steps: int) -> date:
    """Сдвинуть дату периода на steps интервалов вперёд."""
    if interval in _DAYS:
        return value + timedelta(days=_DAYS[interval] * steps)
    months = value.month - 1 + _MONTHS[interval] * steps
    return value.replace(year=value.year + months // 12, month=months % 12 + 1, day=1)


class ForecastArgs(ToolArgs):
    date_column: Ident
    metric_column: Ident
    periods: int = Field(default=3, ge=1, le=100)
    interval: Interval | None = None
    function: Literal["SUM", "AVG"] = "SUM"
    include_history: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict):
            if isinstance(data.get("interval"), str):
                data["interval"] = data["interval"].lower()
            if isinstance(data.get("function"), str):
                data["function"] = data["function"].upper()
        return data


class ForecastTool(BaseTool):
    """Прогноз на N периодов вперёд по линейному тренду."""

    name = "forecast"
    description = (
        "Прогноз метрики на periods периодов вперёд по линейному тренду; interval "
        "агрегирует ряд по периодам (function SUM/AVG) и даёт даты будущих периодов; "
        "include_history добавляет фактические значения (kind=actual/forecast)"
    )
    Args = ForecastArgs

    def execute(self, db, input_table: str, args: ForecastArgs) -> str:
        period_sql = (
            f"date_trunc('{args.interval}', {args.date_column})"
            if args.interval
            else args.date_column
        )
        rows = db.query(
            f"SELECT {period_sql} AS period, {args.function}({args.metric_column}) AS value "
            f"FROM {identifier(input_table)} WHERE {args.date_column} IS NOT NULL "
            "GROUP BY period ORDER BY period"
        )
        history = [(row["period"], float(row["value"])) for row in rows if row["value"] is not None]
        if len(history) < 2:
            raise ValueError("forecast requires at least two observations")
        values = [value for _, value in history]
        n = len(values)
        x_mean, y_mean = (n - 1) / 2, sum(values) / n
        slope = sum((x - x_mean) * (y - y_mean) for x, y in enumerate(values)) / sum(
            (x - x_mean) ** 2 for x in range(n)
        )
        last = history[-1][0]
        can_date = args.interval is not None and isinstance(last, (date, datetime))
        result = (
            [(index, period, value, "actual") for index, (period, value) in enumerate(history)]
            if args.include_history
            else []
        )
        for step in range(1, args.periods + 1):
            x = n - 1 + step
            period = shift(last, args.interval, step) if can_date else None  # type: ignore[arg-type]
            result.append((x, period, y_mean + slope * (x - x_mean), "forecast"))
        return rows_result(db, input_table, ["period_index", "period", "value", "kind"], result)
