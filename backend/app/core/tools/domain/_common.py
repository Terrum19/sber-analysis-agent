"""Shared helpers for domain tools."""

from datetime import date, datetime

import numpy as np
from pydantic import Field
from scipy import stats

from app.core.tools.base import Ident, ToolArgs, identifier
from app.core.tools.stats._common import rows_result


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


def trend(values: list[float]) -> tuple[float, float, float]:
    x = np.arange(len(values), dtype=float)
    if len(values) < 2 or np.ptp(values) == 0:
        return 0.0, float(np.mean(values)), 0.0
    fitted = stats.linregress(x, np.asarray(values))
    return float(fitted.slope), float(fitted.intercept), float(fitted.rvalue**2)


def period_index(value: object, position: int = 0, seasonality_period: int = 12) -> int:
    if seasonality_period == 12 and isinstance(value, (date, datetime)):
        return value.month
    text = str(value)
    if seasonality_period == 12 and len(text) >= 7 and text[4] == "-":
        return int(text[5:7])
    return position % seasonality_period + 1


__all__ = [
    "Field",
    "SeriesArgs",
    "identifier",
    "observations",
    "period_index",
    "rows_result",
    "trend",
]
