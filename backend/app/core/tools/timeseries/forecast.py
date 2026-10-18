"""TS-тул: экстраполяция/прогноз (scenario-based).

Пример сценария: «Какой рост X ожидается в ближайшие кварталы при
текущих условиях?», «Как изменится Y, если изменить цену X?».
"""

import statistics

from app.core.tools.base import BaseTool, identifier
from app.core.tools.stats._common import rows_result


class ForecastTool(BaseTool):
    """Прогноз на N периодов вперёд по линейному тренду."""

    name = "forecast"
    description = (
        "Экстраполировать метрику на ближайшие периоды линейным трендом; "
        "columns=[date_column, metric_column, periods?]"
    )

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) < 2:
            raise ValueError("forecast requires [date_column, metric_column, periods?]")
        date_col, metric_col = identifier(columns[0]), identifier(columns[1])
        periods = int(kwargs.get("periods", columns[2] if len(columns) > 2 else 3))
        if periods < 1 or periods > 100:
            raise ValueError("forecast periods must be between 1 and 100")
        rows = db.query(
            f"SELECT {date_col} AS period, AVG({metric_col}) AS value "
            f"FROM {identifier(input_table)} GROUP BY period ORDER BY period"
        )
        values = [float(row["value"]) for row in rows if row["value"] is not None]
        if len(values) < 2:
            raise ValueError("forecast requires at least two observations")
        position_mean, value_mean = statistics.fmean(range(len(values))), statistics.fmean(values)
        slope = sum(
            (position - position_mean) * (value - value_mean)
            for position, value in enumerate(values)
        ) / sum((position - position_mean) ** 2 for position in range(len(values)))
        predictions = [
            (
                len(values) + index + 1,
                value_mean + slope * (len(values) + index - position_mean),
            )
            for index in range(periods)
        ]
        return rows_result(db, input_table, ["period_index", "forecast"], predictions)
