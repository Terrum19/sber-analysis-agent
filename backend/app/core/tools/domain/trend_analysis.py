"""Linear trend with monthly seasonal indices."""

from app.core.tools.base import BaseTool
from app.core.tools.domain._common import observations, period_index, required, rows_result, trend


class TrendAnalysisTool(BaseTool):
    name = "trend_analysis"
    description = (
        "Линейный тренд и сезонные индексы временного ряда; parameters: seasonality_period"
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
        values = [value for _, value in observations_]
        slope, intercept, r_squared = trend(values)
        seasonal = {}
        for period in range(1, seasonality_period + 1):
            period_values = [
                value
                for position, (timestamp, value) in enumerate(observations_)
                if period_index(timestamp, position, seasonality_period) == period
            ]
            seasonal[period] = (
                sum(period_values) / len(period_values) / (sum(values) / len(values))
                if period_values and sum(values)
                else 1.0
            )
        rows = []
        for index, (timestamp, value) in enumerate(observations_):
            fitted = intercept + slope * index
            rows.append(
                (
                    timestamp,
                    value,
                    fitted,
                    seasonal[period_index(timestamp, index, seasonality_period)],
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
