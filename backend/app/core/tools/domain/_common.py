"""Shared helpers for domain tools."""

import math
import re
from datetime import date, datetime
from itertools import pairwise
from typing import Literal, NamedTuple

import numpy as np
from pydantic import model_validator
from scipy import stats

from app.core.tools.base import Ident, ToolArgs, identifier
from app.core.tools.sql.atomic import aggregate_sql
from app.core.tools.stats._common import rows_result

_YEAR_MONTH = re.compile(r"\d{4}-\d{2}")
_MONDAY = date(1970, 1, 5)

SeriesFunction = Literal["SUM", "COUNT", "AVG"]

SEASONS = {"hour": 24, "day": 7, "week": 52, "month": 12, "quarter": 4}
"""Длина сезона по шагу ряда: сутки по часам, неделя по дням, год по неделям, месяцам, кварталам."""

_TOLERANCE = {"hour": "1 MINUTE", "day": "1 HOUR"}
_GRAINS = ("year", "quarter", "month", "week", "day", "hour")


class SeriesArgs(ToolArgs):
    """Ряд «дата — значение».

    interval, function и by объявляют наследники после своих полей, чтобы
    позиционные аргументы не сдвинулись.
    """

    date_column: Ident
    value_column: Ident

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict):
            data = dict(data)
            if isinstance(data.get("interval"), str):
                data["interval"] = data["interval"].lower() or None
            if isinstance(data.get("function"), str):
                data["function"] = data["function"].upper()
            if isinstance(data.get("by"), str):
                data["by"] = [data["by"]]
        return data


class Point(NamedTuple):
    period: object
    value: float
    step: int
    incomplete: bool


def series(db, table: str, args, tool: str) -> tuple[str | None, list[tuple[tuple, list[Point]]]]:
    """Ряды по разрезам by и шаг ряда (hour, day, week, month, quarter, year или None).

    С interval строки сворачиваются function по периодам, а крайний период,
    который данные покрывают не целиком, помечается incomplete. Без interval
    повтор даты внутри ряда — ошибка: на входе построчные данные. Имена после
    time_bucket (period, sum_<колонка>) узнаются, если планировщик назвал
    исходные.
    """
    described = db.query(f"DESCRIBE {identifier(table)}")
    columns = {row["column_name"]: row["column_type"] for row in described}
    date_column = _resolve(args.date_column, columns, ["period"])
    value_column = args.value_column and _resolve(
        args.value_column,
        columns,
        [name for name in columns if name.lower().endswith(f"_{args.value_column}".lower())],
    )
    by = [identifier(column) for column in args.by]
    keys = "".join(f"{column}, " for column in by)
    when = identifier(date_column)
    if args.interval:
        moment = f"date_trunc('{args.interval}', {when})"
        measure = aggregate_sql(args.function, value_column)
        sql = (
            f"SELECT {keys}{moment} AS event_date, {measure} AS value FROM {identifier(table)} "
            f"WHERE {when} IS NOT NULL GROUP BY ALL ORDER BY ALL"
        )
    else:
        sql = (
            f"SELECT {keys}{when} AS event_date, {identifier(value_column)} AS value "
            f"FROM {identifier(table)} ORDER BY {keys}{when}"
        )
    groups: dict[tuple, list[tuple[object, float]]] = {}
    for row in db.query(sql):
        if row["event_date"] is not None and row["value"] is not None:
            key = tuple(row[column] for column in args.by)
            groups.setdefault(key, []).append((row["event_date"], float(row["value"])))
    if not groups:
        raise ValueError(f"{tool} requires observations")
    if not args.interval:
        _reject_raw_rows(groups, columns, [date_column, value_column, *args.by], tool)
    partial = _partial_edges(db, table, when, args.interval)
    unit = args.interval or _unit([moment for points in groups.values() for moment, _ in points])
    result = []
    for key, points in groups.items():
        steps = step_index([moment for moment, _ in points], unit)
        result.append(
            (
                key,
                [
                    Point(moment, value, step, moment in partial)
                    for (moment, value), step in zip(points, steps)
                ],
            )
        )
    return unit, result


def _resolve(name: str, columns: dict[str, str], fallbacks: list[str]) -> str:
    if name in columns:
        return name
    present = [column for column in fallbacks if column in columns]
    return present[0] if len(present) == 1 else name


def _reject_raw_rows(
    groups: dict[tuple, list[tuple[object, float]]],
    columns: dict[str, str],
    used: list[str],
    tool: str,
) -> None:
    rows = sum(len(points) for points in groups.values())
    dates = sum(len({moment for moment, _ in points}) for points in groups.values())
    if rows == dates:
        return
    cuts = [
        name
        for name, kind in columns.items()
        if name not in used and kind.upper().startswith("VARCHAR")
    ]
    hint = (
        f" Если ряд разбит по разрезам ({', '.join(cuts)}), укажи их в by."
        if cuts and len(columns) <= 6
        else ""
    )
    raise ValueError(
        f"{tool}: даты в ряду повторяются ({rows} строк на {dates} дат) — на входе "
        "построчные данные. Укажи interval (hour/day/week/month/quarter/year) и function "
        '(SUM/COUNT/AVG), например interval="month", function="SUM", или сначала '
        f"time_bucket по периоду.{hint}"
    )


def _partial_edges(db, table: str, when: str, interval: str | None) -> set[object]:
    """Первый и последний периоды, которые данные покрывают не целиком.

    Данные кончаются там, где кончается их собственная точность: даты
    «2010-07-01» у помесячных данных покрывают весь июль, а не одно 1 июля.
    """
    if not interval:
        return set()
    tolerance = f"INTERVAL {_TOLERANCE.get(interval, '1 DAY')}"
    grains = "".join(
        f"WHEN bool_and({when} = date_trunc('{grain}', {when})) "
        f"THEN max({when}) + INTERVAL 1 {grain} "
        for grain in _GRAINS
    )
    row = db.query(
        f"SELECT date_trunc('{interval}', min({when})) AS first, "
        f"min({when}) > date_trunc('{interval}', min({when})) + {tolerance} AS first_partial, "
        f"date_trunc('{interval}', max({when})) AS last, "
        f"CASE {grains}ELSE max({when}) END "
        f"< date_trunc('{interval}', max({when})) + INTERVAL 1 {interval} "
        f"- {tolerance} AS last_partial FROM {identifier(table)} WHERE {when} IS NOT NULL"
    )[0]
    partial = set()
    if row["first_partial"]:
        partial.add(row["first"])
    if row["last_partial"]:
        partial.add(row["last"])
    return partial


def _unit(moments: list[object]) -> str | None:
    """Шаг ряда по самим датам: начала лет, кварталов, месяцев, недель, дней или часов."""
    if moments and all(
        isinstance(moment, str) and _YEAR_MONTH.fullmatch(moment) for moment in moments
    ):
        return "month"
    if not moments or not all(isinstance(moment, date) for moment in moments):
        return None
    times = [moment for moment in moments if isinstance(moment, datetime)]
    if any(moment.minute or moment.second or moment.microsecond for moment in times):
        return None
    if any(moment.hour for moment in times):
        return "hour"
    if all(moment.day == 1 for moment in moments):
        months = sorted({moment.year * 12 + moment.month - 1 for moment in moments})
        gap = math.gcd(*[after - before for before, after in pairwise(months)]) or 1
        if gap % 12 == 0:
            return "year"
        if gap % 3 == 0 and all(moment.month in (1, 4, 7, 10) for moment in moments):
            return "quarter"
        return "month"
    days = sorted({_day(moment) for moment in moments})
    gap = math.gcd(*[after - before for before, after in pairwise(days)]) or 1
    return "week" if gap % 7 == 0 else "day"


def _day(moment: date) -> int:
    day = moment.date() if isinstance(moment, datetime) else moment
    return (day - _MONDAY).days


def step_index(moments: list[object], unit: str | None) -> list[int]:
    """Номер шага ряда по календарю: пропущенный месяц — дырка, а не сдвиг соседей.

    Без шага (не даты) — числа как есть, иначе позиция строки.
    """
    if unit is not None and all(isinstance(moment, date) for moment in moments):
        if unit == "year":
            return [moment.year for moment in moments]
        if unit == "quarter":
            return [moment.year * 4 + (moment.month - 1) // 3 for moment in moments]
        if unit == "month":
            return [moment.year * 12 + moment.month - 1 for moment in moments]
        if unit == "week":
            return [_day(moment) // 7 for moment in moments]
        if unit == "day":
            return [_day(moment) for moment in moments]
        return [
            _day(moment) * 24 + (moment.hour if isinstance(moment, datetime) else 0)
            for moment in moments
        ]
    if unit == "month" and all(isinstance(moment, str) for moment in moments):
        return [int(moment[:4]) * 12 + int(moment[5:7]) - 1 for moment in moments]
    if moments and all(
        isinstance(moment, int) and not isinstance(moment, bool) for moment in moments
    ):
        return list(moments)
    return list(range(len(moments)))


def trend(axis: list[float], values: list[float]) -> tuple[float, float, float, float | None]:
    """Least-squares line value = intercept + slope * axis, its R² and p-value."""
    if len(values) < 2 or np.ptp(values) == 0 or np.ptp(axis) == 0:
        return 0.0, float(np.mean(values)), 0.0, None
    fitted = stats.linregress(np.asarray(axis), np.asarray(values))
    p_value = float(fitted.pvalue) if len(values) > 2 else None
    return float(fitted.slope), float(fitted.intercept), float(fitted.rvalue**2), p_value


def moving_average(values: dict[int, float], window: int) -> dict[int, float | None]:
    """Centred moving average over calendar steps; an even window uses half weights at both ends.

    A window with a missing step gives None: a gap stays a gap.
    """
    half = window // 2
    averages: dict[int, float | None] = {}
    for center in values:
        steps = range(center - half, center + half + 1)
        if any(step not in values for step in steps):
            averages[center] = None
            continue
        span = [values[step] for step in steps]
        if window % 2:
            averages[center] = sum(span) / window
        else:
            averages[center] = (span[0] / 2 + sum(span[1:-1]) + span[-1] / 2) / window
    return averages


__all__ = [
    "SEASONS",
    "Point",
    "SeriesArgs",
    "SeriesFunction",
    "identifier",
    "moving_average",
    "rows_result",
    "series",
    "step_index",
    "trend",
]
