"""Linear trend, year-over-year change and seasonal indices."""

import numpy as np
from pydantic import Field
from scipy import stats

from app.core.tools.base import BaseTool, Ident
from app.core.tools.domain._common import (
    SEASONS,
    Point,
    SeriesArgs,
    SeriesFunction,
    rows_result,
    series,
    trend,
)
from app.core.tools.sql.atomic import Interval

COLUMNS = [
    "value",
    "trend",
    "seasonal_index",
    "residual",
    "yoy_change",
    "incomplete",
    "slope_per_period",
    "intercept",
    "r_squared",
    "p_value",
    "yoy_change_avg",
    "yoy_p_value",
]


class TrendArgs(SeriesArgs):
    seasonality_period: int | None = Field(default=None, ge=1, le=366)
    interval: Interval | None = None
    function: SeriesFunction = "SUM"
    by: list[Ident] = Field(default_factory=list)


class TrendAnalysisTool(BaseTool):
    name = "trend_analysis"
    description = (
        "Растёт/растет или падает ли метрика (рост, снижение, тренд, динамика): наклон "
        "slope_per_period за шаг ряда и p_value без учёта сезонных пиков, yoy_change — к тому "
        "же периоду прошлого сезона, yoy_change_avg — средний рост год к году; построчные "
        "данные — с interval и function (SUM — суммы и количества, AVG — средние величины "
        "одной записи), by — отдельный тренд по каждому разрезу; seasonality_period по "
        "умолчанию по шагу ряда (12 — месяцы, 4 — кварталы, 7 — дни, 24 — часы)"
    )
    kind = "analysis"
    Args = TrendArgs

    def execute(self, db, input_table: str, args: TrendArgs) -> str:
        unit, groups = series(db, input_table, args, self.name)
        season = args.seasonality_period or SEASONS.get(unit or "")
        rows = []
        for key, points in groups:
            for point, fitted in zip(points, _fit(points, season)):
                rows.append((point.period, *key, point.value, *fitted))
        return rows_result(db, input_table, ["period", *args.by, *COLUMNS], rows)


def _fit(points: list[Point], season: int | None) -> list[tuple]:
    """Колонки COLUMNS после value для каждой точки ряда.

    Неполные периоды показываются, но в тренд, сезонность и рост год к году
    не входят. При двух полных сезонах тренд и сезонные отклонения ищутся
    одной регрессией: рост не уходит в сезонность, декабрьские пики не прячут
    рост.
    """
    complete = [point for point in points if not point.incomplete]
    if not complete:
        raise ValueError("trend_analysis requires complete periods")
    origin = complete[0].step
    axis = [float(point.step - origin) for point in complete]
    overall = sum(point.value for point in complete) / len(complete)
    by_season: dict[int, list[float]] = {}
    for point in complete:
        by_season.setdefault(_season(point, season), []).append(point.value)
    means = {key: sum(values) / len(values) for key, values in by_season.items()}
    if season is not None and len(complete) >= 2 * season:
        slope, intercept, r_squared, p_value, effects = _seasonal_trend(
            axis,
            [point.value for point in complete],
            [_season(point, season) for point in complete],
        )
    else:
        slope, intercept, r_squared, p_value = trend(axis, [point.value for point in complete])
        effects = {}
    values = {point.step: point.value for point in complete}
    changes = {
        point.step: point.value / values[point.step - season] - 1
        for point in complete
        if season and values.get(point.step - season)
    }
    average = sum(changes.values()) / len(changes) if changes else None
    spread = len(set(changes.values())) > 1
    yoy_p = float(stats.ttest_1samp(list(changes.values()), 0).pvalue) if spread else None
    rows = []
    for point in points:
        key = _season(point, season)
        fitted = intercept + slope * (point.step - origin)
        level = overall + effects[key] if key in effects else means.get(key, overall)
        index = level / overall if season and overall else 1.0
        residual = None if point.incomplete else point.value - fitted - effects.get(key, 0.0)
        rows.append(
            (
                fitted,
                index,
                residual,
                None if point.incomplete else changes.get(point.step),
                int(point.incomplete),
                slope,
                intercept,
                r_squared,
                p_value,
                average,
                yoy_p,
            )
        )
    return rows


def _season(point: Point, season: int | None) -> int:
    return point.step % season if season else 0


def _seasonal_trend(
    axis: list[float], values: list[float], seasons: list[int]
) -> tuple[float, float, float, float | None, dict[int, float]]:
    """МНК value = intercept + slope * axis + effect[season] — тренд и сезон вместе.

    Сезонные отклонения в сумме дают ноль; r_squared — по ряду без них,
    p_value — t-тест коэффициента при axis.
    """
    keys = sorted(set(seasons))
    design = np.array(
        [
            [1.0, step, *(float(season == key) for key in keys[1:])]
            for step, season in zip(axis, seasons)
        ]
    )
    target = np.asarray(values)
    coefficients = np.linalg.lstsq(design, target, rcond=None)[0]
    raw = dict(zip(keys, [0.0, *map(float, coefficients[2:])]))
    shift = sum(raw.values()) / len(raw)
    effects = {key: value - shift for key, value in raw.items()}
    slope, intercept = float(coefficients[1]), float(coefficients[0]) + shift
    error = float(np.sum((target - design @ coefficients) ** 2))
    deseasonalized = target - np.array([effects[season] for season in seasons])
    spread = float(np.sum((deseasonalized - deseasonalized.mean()) ** 2))
    r_squared = 1 - error / spread if spread else 0.0
    freedom = len(values) - design.shape[1]
    p_value = None
    if freedom > 0:
        variance = error / freedom * float(np.linalg.pinv(design.T @ design)[1, 1])
        if variance > 0:
            p_value = float(2 * stats.t.sf(abs(slope) / np.sqrt(variance), freedom))
        elif slope:
            p_value = 0.0
    return slope, intercept, r_squared, p_value, effects
