"""Classical additive or multiplicative trend/seasonal/residual decomposition."""

from typing import Literal

from pydantic import Field

from app.core.tools.base import BaseTool
from app.core.tools.domain._common import (
    SeriesArgs,
    moving_average,
    observations,
    period_index,
    rows_result,
)


class DecomposeArgs(SeriesArgs):
    mode: Literal["additive", "multiplicative"] = "additive"
    seasonality_period: int = Field(default=12, ge=1, le=366)


class SeasonalDecomposeTool(BaseTool):
    name = "seasonal_decompose"
    description = (
        "Классическое разложение ряда на trend (скользящее среднее за сезон), seasonal "
        "и residual (additive/multiplicative); нужно не меньше двух полных сезонов"
    )
    kind = "analysis"
    Args = DecomposeArgs

    def execute(self, db, input_table: str, args: DecomposeArgs) -> str:
        mode, seasonality_period = args.mode, args.seasonality_period
        observations_ = observations(db, input_table, args.date_column, args.value_column)
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
