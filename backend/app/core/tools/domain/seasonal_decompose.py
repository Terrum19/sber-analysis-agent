"""Additive or multiplicative trend/seasonal/residual decomposition."""

from app.core.tools.base import BaseTool
from app.core.tools.domain._common import observations, period_index, required, rows_result, trend


class SeasonalDecomposeTool(BaseTool):
    name = "seasonal_decompose"
    description = (
        "Разложение временного ряда на trend, seasonal и residual; parameters: seasonality_period"
    )

    def run(
        self,
        db,
        input_table: str,
        columns: list[str],
        **kwargs: object,
    ) -> str:
        required(columns[:2], 2, 2, self.name)
        observations_ = observations(db, input_table, columns[0], columns[1])
        mode = columns[2].lower() if len(columns) == 3 else "additive"
        seasonality_period = int(
            kwargs.get(
                "seasonality_period",
                columns[3] if len(columns) == 4 else 12,
            )
        )
        if seasonality_period < 1:
            raise ValueError("seasonality_period must be positive")
        if mode not in {"additive", "multiplicative"}:
            raise ValueError("seasonal_decompose mode must be additive or multiplicative")
        if not observations_:
            raise ValueError("seasonal_decompose requires observations")
        values = [value for _, value in observations_]
        if mode == "multiplicative" and any(value <= 0 for value in values):
            raise ValueError("multiplicative decomposition requires positive values")
        slope, intercept, _ = trend(values)
        trend_values = [intercept + slope * index for index in range(len(values))]
        seasonal = {}
        for period in range(1, seasonality_period + 1):
            ratios = []
            for position, ((timestamp, value), trend_value) in enumerate(
                zip(observations_, trend_values)
            ):
                if period_index(timestamp, position, seasonality_period) == period and trend_value:
                    ratios.append(
                        value / trend_value if mode == "multiplicative" else value - trend_value
                    )
            seasonal[period] = (
                sum(ratios) / len(ratios) if ratios else (1.0 if mode == "multiplicative" else 0.0)
            )
        rows = []
        for index, (timestamp, value) in enumerate(observations_):
            seasonal_value = seasonal[period_index(timestamp, index, seasonality_period)]
            fitted = (
                trend_values[index] * seasonal_value
                if mode == "multiplicative"
                else trend_values[index] + seasonal_value
            )
            residual = value / fitted if mode == "multiplicative" else value - fitted
            rows.append((timestamp, trend_values[index], seasonal_value, residual, mode))
        return rows_result(
            db,
            input_table,
            ["period", "trend", "seasonal", "residual", "mode"],
            rows,
        )
