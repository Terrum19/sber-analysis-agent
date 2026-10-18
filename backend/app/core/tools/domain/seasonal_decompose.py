"""Classical additive or multiplicative trend/seasonal/residual decomposition."""

from app.core.tools.base import BaseTool
from app.core.tools.domain._common import (
    moving_average,
    observations,
    period_index,
    required,
    rows_result,
)


class SeasonalDecomposeTool(BaseTool):
    name = "seasonal_decompose"
    description = (
        "Классическое разложение ряда на trend (скользящее среднее за сезон), seasonal "
        "и residual; columns=[date_col, value_col, mode?]; mode additive|multiplicative; "
        "parameters: seasonality_period; нужно не меньше двух полных сезонов"
    )

    def run(
        self,
        db,
        input_table: str,
        columns: list[str],
        **kwargs: object,
    ) -> str:
        required(columns[:2], 2, 2, self.name)
        mode = (
            columns[2].lower() if len(columns) >= 3 else str(kwargs.get("mode", "additive")).lower()
        )
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
        observations_ = observations(db, input_table, columns[0], columns[1])
        if len(observations_) < 2 * seasonality_period:
            raise ValueError(
                f"seasonal_decompose requires at least {2 * seasonality_period} observations "
                f"(two full seasons of {seasonality_period})"
            )
        timestamps = [timestamp for timestamp, _ in observations_]
        values = [value for _, value in observations_]
        multiplicative = mode == "multiplicative"
        if multiplicative and any(value <= 0 for value in values):
            raise ValueError("multiplicative decomposition requires positive values")
        trend_values = moving_average(values, seasonality_period)
        seasons = [
            period_index(timestamp, position, seasonality_period)
            for position, timestamp in enumerate(timestamps)
        ]
        detrended: dict[int, list[float]] = {}
        for value, trend_value, season in zip(values, trend_values, seasons):
            if trend_value is not None:
                deviation = value / trend_value if multiplicative else value - trend_value
                detrended.setdefault(season, []).append(deviation)
        raw_seasonal = {season: sum(items) / len(items) for season, items in detrended.items()}
        center = sum(raw_seasonal.values()) / len(raw_seasonal)
        seasonal = {
            season: value / center if multiplicative else value - center
            for season, value in raw_seasonal.items()
        }
        neutral = 1.0 if multiplicative else 0.0
        rows = []
        for timestamp, value, trend_value, season in zip(timestamps, values, trend_values, seasons):
            seasonal_value = seasonal.get(season, neutral)
            residual = None
            if trend_value is not None:
                residual = (
                    value / (trend_value * seasonal_value)
                    if multiplicative
                    else value - trend_value - seasonal_value
                )
            rows.append((timestamp, value, trend_value, seasonal_value, residual, mode))
        return rows_result(
            db,
            input_table,
            ["period", "value", "trend", "seasonal", "residual", "mode"],
            rows,
        )
