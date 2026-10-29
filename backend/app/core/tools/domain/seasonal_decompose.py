"""Classical additive or multiplicative trend/seasonal/residual decomposition."""

from typing import Literal

from pydantic import Field

from app.core.tools.base import BaseTool, Ident
from app.core.tools.domain._common import (
    SEASONS,
    Point,
    SeriesArgs,
    SeriesFunction,
    moving_average,
    rows_result,
    series,
)
from app.core.tools.sql.atomic import Interval


class DecomposeArgs(SeriesArgs):
    mode: Literal["additive", "multiplicative"] = "additive"
    seasonality_period: int | None = Field(default=None, ge=1, le=366)
    interval: Interval | None = None
    function: SeriesFunction = "SUM"
    by: list[Ident] = Field(default_factory=list)


class SeasonalDecomposeTool(BaseTool):
    name = "seasonal_decompose"
    description = (
        "Классическое разложение ряда на trend (скользящее среднее за сезон), seasonal "
        "и residual (additive/multiplicative); нужно не меньше двух полных сезонов; "
        "построчные данные — с interval и function (SUM/COUNT/AVG), by — по каждому разрезу; "
        "seasonality_period по умолчанию по шагу ряда (12 — месяцы, 7 — дни, 24 — часы)"
    )
    kind = "analysis"
    Args = DecomposeArgs

    def execute(self, db, input_table: str, args: DecomposeArgs) -> str:
        unit, groups = series(db, input_table, args, self.name)
        season = args.seasonality_period or SEASONS.get(unit or "")
        if season is None:
            raise ValueError("seasonal_decompose: укажи seasonality_period — у ряда нет сезона")
        rows = []
        for key, points in groups:
            for point, parts in zip(points, _decompose(points, season, args.mode)):
                rows.append((point.period, *key, point.value, *parts, args.mode))
        return rows_result(
            db,
            input_table,
            ["period", *args.by, "value", "trend", "seasonal", "residual", "incomplete", "mode"],
            rows,
        )


def _decompose(points: list[Point], season: int, mode: str) -> list[tuple]:
    """trend, seasonal, residual и incomplete для каждой точки; неполные периоды не считаются."""
    complete = [point for point in points if not point.incomplete]
    if len(complete) < 2 * season:
        raise ValueError(
            f"seasonal_decompose requires at least {2 * season} complete observations "
            f"(two full seasons of {season})"
        )
    values = [point.value for point in complete]
    multiplicative = mode == "multiplicative"
    if multiplicative and any(value <= 0 for value in values):
        raise ValueError("multiplicative decomposition requires positive values")
    trend_values = dict(zip((point.step for point in complete), moving_average(values, season)))
    detrended: dict[int, list[float]] = {}
    for point in complete:
        trend_value = trend_values[point.step]
        if trend_value is not None:
            deviation = point.value / trend_value if multiplicative else point.value - trend_value
            detrended.setdefault(point.step % season, []).append(deviation)
    raw_seasonal = {key: sum(items) / len(items) for key, items in detrended.items()}
    center = sum(raw_seasonal.values()) / len(raw_seasonal)
    seasonal = {
        key: value / center if multiplicative else value - center
        for key, value in raw_seasonal.items()
    }
    neutral = 1.0 if multiplicative else 0.0
    rows = []
    for point in points:
        seasonal_value = seasonal.get(point.step % season, neutral)
        trend_value = None if point.incomplete else trend_values[point.step]
        residual = None
        if trend_value is not None:
            residual = (
                point.value / (trend_value * seasonal_value)
                if multiplicative
                else point.value - trend_value - seasonal_value
            )
        rows.append((trend_value, seasonal_value, residual, int(point.incomplete)))
    return rows
