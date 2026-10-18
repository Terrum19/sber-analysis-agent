"""Linear trend with seasonal indices."""

from app.core.tools.base import BaseTool
from app.core.tools.domain._common import (
    observations,
    period_index,
    required,
    rows_result,
    time_axis,
    trend,
)


class TrendAnalysisTool(BaseTool):
    name = "trend_analysis"
    description = (
        "Линейный тренд по времени (slope — за день для дат) и сезонные индексы; "
        "columns=[date_col, value_col]; parameters: seasonality_period "
        "(12 — месяцы, 4 — кварталы, 7 — дни недели)"
    )

    def run(
        self,
        db,
        input_table: str,
        columns: list[str],
        **kwargs: object,
    ) -> str:
        required(columns[:2], 2, 2, self.name)
        seasonality_period = int(
            kwargs.get("seasonality_period", columns[2] if len(columns) == 3 else 12)
        )
        if seasonality_period < 1:
            raise ValueError("seasonality_period must be positive")
        observations_ = observations(db, input_table, columns[0], columns[1])
        if not observations_:
            raise ValueError("trend_analysis requires observations")
        timestamps = [timestamp for timestamp, _ in observations_]
        values = [value for _, value in observations_]
        axis = time_axis(timestamps)
        slope, intercept, r_squared = trend(axis, values)
        seasons = [
            period_index(timestamp, position, seasonality_period)
            for position, timestamp in enumerate(timestamps)
        ]
        overall = sum(values) / len(values)
        seasonal = {}
        for season in set(seasons):
            season_values = [value for value, other in zip(values, seasons) if other == season]
            seasonal[season] = sum(season_values) / len(season_values) / overall if overall else 1.0
        rows = []
        for timestamp, value, position, season in zip(timestamps, values, axis, seasons):
            fitted = intercept + slope * position
            rows.append(
                (
                    timestamp,
                    value,
                    fitted,
                    seasonal[season],
                    value - fitted,
                    slope,
                    intercept,
                    r_squared,
                )
            )
        return rows_result(
            db,
            input_table,
            [
                "period",
                "value",
                "trend",
                "seasonal_index",
                "residual",
                "slope",
                "intercept",
                "r_squared",
            ],
            rows,
        )
