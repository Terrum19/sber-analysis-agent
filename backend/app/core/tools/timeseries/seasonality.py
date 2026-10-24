"""TS-тул: сезонность (средний уровень по календарному периоду).

Пример сценария: «Какой паттерн повторяется каждые 12 месяцев?»,
«В какой день недели больше заказов?».
"""

from typing import Literal

from app.core.tools.base import BaseTool, Ident, MetricArgs, identifier

_PARTS = {"month": "month", "quarter": "quarter", "dayofweek": "isodow", "hour": "hour"}


class SeasonalityArgs(MetricArgs):
    date_column: Ident
    metric_column: Ident
    period: Literal["month", "quarter", "dayofweek", "hour"] = "month"


class SeasonalityTool(BaseTool):
    """Среднее по календарному периоду для поиска повторяемости."""

    name = "seasonality"
    description = "Сезонные пики: средний уровень метрики по month/quarter/dayofweek (1=пн)/hour"
    Args = SeasonalityArgs

    def execute(self, db, input_table: str, args: SeasonalityArgs) -> str:
        part = _PARTS[args.period]
        sql = (
            f"SELECT date_part('{part}', {identifier(args.date_column)}) AS {args.period}, "
            f"AVG({identifier(args.metric_column)}) AS avg_level, COUNT(*) AS n "
            f"FROM {identifier(input_table)} GROUP BY 1 ORDER BY 1"
        )
        return db.save_step_result(input_table, sql)
