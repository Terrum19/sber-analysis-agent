"""TS-тул: экстраполяция/прогноз (scenario-based).

Пример сценария: «Какой рост X ожидается в ближайшие кварталы при
текущих условиях?», «Как изменится Y, если изменить цену X?».
"""

from app.core.tools.base import BaseTool
from app.core.tools.base import identifier
import statistics


class ForecastTool(BaseTool):
    """Прогноз на N периодов вперёд по линейному тренду."""

    name = "forecast"
    description = "Экстраполировать метрику на ближайшие периоды (тренд + сценарии)"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) < 2:
            raise ValueError("forecast requires [date_column, metric_column, periods?]")
        date_col, metric_col = identifier(columns[0]), identifier(columns[1])
        periods = int(columns[2]) if len(columns) > 2 else 3
        if periods < 1 or periods > 100:
            raise ValueError("forecast periods must be between 1 and 100")
        rows = db.query(
            f"SELECT {date_col} AS period, AVG({metric_col}) AS value "
            f"FROM {identifier(input_table)} GROUP BY period ORDER BY period"
        )
        values = [float(row["value"]) for row in rows if row["value"] is not None]
        if len(values) < 2:
            raise ValueError("forecast requires at least two observations")
        x_mean, y_mean = statistics.fmean(range(len(values))), statistics.fmean(values)
        slope = sum((x - x_mean) * (y - y_mean) for x, y in enumerate(values)) / sum(
            (x - x_mean) ** 2 for x in range(len(values))
        )
        predictions = [(len(values) + index + 1, y_mean + slope * (len(values) + index)) for index in range(periods)]
        values_sql = ", ".join(f"({period}, {prediction})" for period, prediction in predictions)
        return db.save_step_result(
            input_table,
            f"SELECT * FROM (VALUES {values_sql}) AS forecast(period_index, forecast)",
        )
