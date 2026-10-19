"""TS-тул: сезонность (пики по месяцам/дням года).

Пример сценария: «Какой паттерн повторяется каждые 12 месяцев?»
"""

from app.core.tools.base import BaseTool, identifier


class SeasonalityTool(BaseTool):
    """Среднее по календарному периоду для поиска повторяемости."""

    name = "seasonality"
    description = "Найти сезонные пики: среднее значение по месяцам/дням года"

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) < 2:
            raise ValueError("seasonality requires [date_column, metric_column]")
        time_col, metric = identifier(columns[0]), identifier(columns[1])
        sql = (
            f"SELECT month({time_col}) AS month, AVG({metric}) AS avg_level, COUNT(*) AS n "
            f"FROM {identifier(input_table)} GROUP BY month ORDER BY month"
        )
        return db.save_step_result(input_table, sql)
