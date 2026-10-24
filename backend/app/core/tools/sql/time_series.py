"""SQL-тул: агрегация временного ряда (по месяцу/кварталу).

Пример сценария: «Что происходит с X каждый июль?»,
«Есть ли сезонные пики зимой?» — база для дашбордов.
Оставлен для совместимости; планировщику показывается time_bucket.
"""

from typing import Literal

from pydantic import model_validator

from app.core.tools.base import BaseTool, Ident, MetricArgs, identifier
from app.core.tools.sql.atomic import Interval


class TimeSeriesArgs(MetricArgs):
    date_column: Ident
    metric_column: Ident
    interval: Interval = "month"
    function: Literal["AVG", "SUM", "MIN", "MAX", "MEDIAN"] = "AVG"

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict):
            if isinstance(data.get("interval"), str):
                data["interval"] = data["interval"].lower()
            if isinstance(data.get("function"), str):
                data["function"] = data["function"].upper()
        return data


class TimeSeriesTool(BaseTool):
    """Агрегация метрики по временным бакетам."""

    name = "time_series"
    description = "Метрика по периодам: metric_{function} и n в колонке period"
    Args = TimeSeriesArgs

    def execute(self, db, input_table: str, args: TimeSeriesArgs) -> str:
        sql = (
            f"SELECT date_trunc('{args.interval}', {identifier(args.date_column)}) AS period, "
            f"{args.function}({identifier(args.metric_column)}) "
            f"AS metric_{args.function.lower()}, "
            f"COUNT(*) AS n FROM {identifier(input_table)} GROUP BY period ORDER BY period"
        )
        return db.save_step_result(input_table, sql)
