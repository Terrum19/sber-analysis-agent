"""SQL-тул: агрегация временного ряда (по месяцу/кварталу).

Пример сценария: «Что происходит с X каждый июль?»,
«Есть ли сезонные пики зимой?" — база для дашбордов.
"""

from app.core.tools.base import BaseTool, identifier


class TimeSeriesTool(BaseTool):
    """Агрегация метрики по временным бакетам."""

    name = "time_series"
    description = (
        "Агрегировать метрику по month/quarter/week/day; "
        "columns=[date_column, metric_column, interval?]"
    )

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if not columns:
            raise ValueError("time_series requires [date_column, metric_column, interval?]")
        time_col, metric = columns[0], columns[1] if len(columns) > 1 else columns[0]
        interval = str(kwargs.get("interval", columns[2] if len(columns) > 2 else "month")).lower()
        if interval not in {"month", "quarter", "week", "day"}:
            raise ValueError("time_series interval must be month, quarter, week or day")
        sql = (
            f"SELECT date_trunc('{interval}', {identifier(time_col)}) AS period, "
            f"AVG({identifier(metric)}) AS metric_avg, COUNT(*) AS n "
            f"FROM {identifier(input_table)} GROUP BY period ORDER BY period"
        )
        return db.save_step_result(input_table, sql)
