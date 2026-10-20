"""Shared helpers for domain tools."""

import numbers
import re
from datetime import date, datetime

import numpy as np
from scipy import stats

from app.core.tools.base import Ident, ToolArgs, identifier
from app.core.tools.stats._common import rows_result

_YEAR_MONTH = re.compile(r"^\d{4}-\d{2}")


class SeriesArgs(ToolArgs):
    date_column: Ident
    value_column: Ident


def observations(db, table: str, date_col: str, value_col: str) -> list[tuple[object, float]]:
    rows = db.query(
        f"SELECT {identifier(date_col)} AS event_date, {identifier(value_col)} AS value "
        f"FROM {identifier(table)} ORDER BY {identifier(date_col)}"
    )
    return [
        (row["event_date"], float(row["value"]))
        for row in rows
        if row["event_date"] is not None and row["value"] is not None
    ]


def time_axis(timestamps: list[object]) -> list[float]:
    """Positions on a time axis: days for dates, the value itself for numbers.

    Anything else (text periods such as "2024-01") falls back to the row
    position, which assumes evenly spaced observations.
    """
    if timestamps and all(isinstance(value, datetime) for value in timestamps):
        start = timestamps[0]
        return [(value - start).total_seconds() / 86400 for value in timestamps]
    if timestamps and all(
        isinstance(value, date) and not isinstance(value, datetime) for value in timestamps
    ):
        start = timestamps[0]
        return [float((value - start).days) for value in timestamps]
    if timestamps and all(
        isinstance(value, numbers.Real) and not isinstance(value, bool) for value in timestamps
    ):
        return [float(value) for value in timestamps]
    return [float(position) for position in range(len(timestamps))]


def trend(axis: list[float], values: list[float]) -> tuple[float, float, float]:
    """Least-squares line value = intercept + slope * axis and its R²."""
    if len(values) < 2 or np.ptp(values) == 0 or np.ptp(axis) == 0:
        return 0.0, float(np.mean(values)), 0.0
    fitted = stats.linregress(np.asarray(axis), np.asarray(values))
    return float(fitted.slope), float(fitted.intercept), float(fitted.rvalue**2)


def period_index(value: object, position: int = 0, seasonality_period: int = 12) -> int:
    """Season of an observation, from 1 to seasonality_period.

    Dates use the calendar for the usual periods: month for 12, quarter for 4,
    weekday for 7. Other periods and non-date values use the row position.
    """
    if isinstance(value, (date, datetime)):
        if seasonality_period == 12:
            return value.month
        if seasonality_period == 4:
            return (value.month - 1) // 3 + 1
        if seasonality_period == 7:
            return value.isoweekday()
    if seasonality_period == 12 and _YEAR_MONTH.match(str(value)):
        return int(str(value)[5:7])
    return position % seasonality_period + 1


def moving_average(values: list[float], window: int) -> list[float | None]:
    """Centred moving average; an even window uses half weights at both ends."""
    if window == 1:
        return list(values)
    half = window // 2
    averages: list[float | None] = [None] * len(values)
    for center in range(half, len(values) - half):
        span = values[center - half : center + half + 1]
        if window % 2:
            averages[center] = sum(span) / window
        else:
            averages[center] = (span[0] / 2 + sum(span[1:-1]) + span[-1] / 2) / window
    return averages


__all__ = [
    "SeriesArgs",
    "identifier",
    "moving_average",
    "observations",
    "period_index",
    "rows_result",
    "time_axis",
    "trend",
]
