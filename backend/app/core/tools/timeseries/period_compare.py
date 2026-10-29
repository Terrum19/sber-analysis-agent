from typing import Literal

from pydantic import model_validator

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier

_PARTS = {"month": "month", "quarter": "quarter", "weekday": "isodow"}
_AGGREGATES = {"sum": "SUM", "avg": "AVG", "count": "COUNT", "min": "MIN", "max": "MAX"}


class PeriodCompareArgs(ToolArgs):
    date: Ident
    value: Ident
    part: Literal["month", "quarter", "weekday"]
    agg: Literal["sum", "avg", "count", "min", "max"] = "sum"

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict):
            for field_name in ("part", "agg"):
                if isinstance(data.get(field_name), str):
                    data[field_name] = data[field_name].lower()
        return data


class PeriodCompareTool(BaseTool):
    name = "period_compare"
    description = (
        "Один и тот же период по годам: что происходит каждый июль, каждый декабрь, "
        "каждый квартал или день недели, выделяется ли месяц на фоне остальных; "
        "строка на год и part (month 1-12, quarter 1-4, weekday 1=пн), value — agg "
        "метрики, part_mean и deviation — среднее этого part по всем годам и отклонение "
        "от него, year_mean и vs_year_mean_pct — средний part этого года и отклонение "
        "от него в процентах. Пример: «Что происходит с выручкой каждый июль?»"
    )
    kind = "transform"
    Args = PeriodCompareArgs

    def execute(self, db, input_table: str, args: PeriodCompareArgs) -> str:
        date_column = identifier(args.date)
        aggregate = _AGGREGATES[args.agg]
        grouped = (
            f"SELECT year({date_column}) AS year, "
            f"date_part('{_PARTS[args.part]}', {date_column}) AS part, "
            f"{aggregate}({identifier(args.value)}) AS value "
            f"FROM {identifier(input_table)} WHERE {date_column} IS NOT NULL GROUP BY 1, 2"
        )
        sql = (
            "SELECT year, part, value, "
            "AVG(value) OVER (PARTITION BY part) AS part_mean, "
            "value - AVG(value) OVER (PARTITION BY part) AS deviation, "
            "AVG(value) OVER (PARTITION BY year) AS year_mean, "
            "100 * (value / NULLIF(AVG(value) OVER (PARTITION BY year), 0) - 1) "
            f"AS vs_year_mean_pct FROM ({grouped}) ORDER BY year, part"
        )
        return db.save_step_result(input_table, sql)
