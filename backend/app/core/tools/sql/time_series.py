"""SQL-тул: агрегация временного ряда (по месяцу/кварталу).

Пример сценария: «Что происходит с X каждый июль?»,
«Есть ли сезонные пики зимой?" — база для дашбордов.
"""
from app.core.tools.base import BaseTool


class TimeSeriesTool(BaseTool):
    """Агрегация метрики по временным бакетам."""

    name = "time_series"
    description = "Агрегировать метрику по месяцу/кварталу для анализа сезонности"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        time_col, metric = columns[0], columns[1] if len(columns) > 1 else columns[0]
        sql = (
            f"SELECT date_trunc('month', {time_col}) AS period, "
            f"AVG({metric}) AS metric_avg, COUNT(*) AS n "
            f"FROM {input_table} GROUP BY period ORDER BY period"
        )
        return db.save_step_result(input_table, sql)
