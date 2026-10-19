"""Linear trend with monthly seasonal indices."""

from app.core.tools.base import BaseTool
from app.core.tools.domain._common import (
    Field,
    SeriesArgs,
    observations,
    period_index,
    rows_result,
    trend,
)


class TrendArgs(SeriesArgs):
    seasonality_period: int = Field(default=12, ge=1, le=366)


class TrendAnalysisTool(BaseTool):
    name = "trend_analysis"
    description = "Линейный тренд (slope, r_squared) и сезонные индексы временного ряда"
    kind = "analysis"
    Args = TrendArgs

    def execute(self, db, input_table: str, args: TrendArgs) -> str:
        seasonality_period = args.seasonality_period
        observations_ = observations(db, input_table, args.date_column, args.value_column)
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
