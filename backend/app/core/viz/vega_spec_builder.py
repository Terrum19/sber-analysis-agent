"""Построитель Vega-Lite спецификаций из таблицы результата.

Один инсайт -> JSON-конфиг (Vega-Lite v5), который читается фронтендом
(react-vega) без дополнительной серверной отрисовки. Данные берутся из
temp_result_vN (или передаются готовыми записями), типы каналов
определяются по значениям: даты -> temporal, числа -> quantitative,
остальное -> nominal. Если данных не хватает для запрошенного вида,
спека строится в ближайшем подходящем виде (см. usermeta.chart_type).
"""

import datetime as dt
import math
import re
from decimal import Decimal
from typing import Any

VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"
SPEC_ROW_LIMIT = 500
CHART_TYPES = ("bar", "line", "area", "scatter", "histogram", "box", "heatmap", "table")
COUNT_TITLE = "Количество"
ROW_FIELD = "_row"
COLUMN_FIELD = "_column"
VALUE_FIELD = "_value"

_DATE_TEXT = re.compile(r"^\d{4}-\d{2}(-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?)?$")
_ORDER_NAME = re.compile(
    r"(year|month|quarter|week|day|date|period|time|год|месяц|квартал|недел|день|дата|период)",
    re.IGNORECASE,
)
_TOOLTIP_KEYS = ("field", "type", "title", "aggregate", "bin")
_FIELD_ESCAPE = re.compile(r"([.\[\]\\])")
_MARKS = {
    "bar": "bar",
    "line": "line",
    "area": "area",
    "scatter": "point",
    "histogram": "bar",
    "box": "boxplot",
    "heatmap": "rect",
    "table": "text",
}


def column_aliases(passport: dict) -> dict[str, str]:
    """Читаемые названия колонок из паспорта данных (ProfileMemory)."""
    titles: dict[str, str] = {}
    for key in ("glossary", "titles"):
        mapping = passport.get(key)
        if not isinstance(mapping, dict):
            continue
        for column, alias in mapping.items():
            if isinstance(alias, list | tuple):
                alias = alias[0] if alias else ""
            label = alias.strip() if isinstance(alias, str) else ""
            if label:
                titles[str(column)] = label[0].upper() + label[1:]
    return titles


def to_json_value(value: Any) -> Any:
    """Привести значение из DuckDB к JSON-совместимому виду."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, dt.date | dt.time):
        return value.isoformat()
    return str(value)


def infer_field_type(values: list[Any]) -> str:
    """Тип канала Vega-Lite по значениям колонки."""
    present = [value for value in values if value is not None]
    if not present:
        return "nominal"
    if all(isinstance(value, dt.date) for value in present):
        return "temporal"
    if all(
        isinstance(value, int | float | Decimal) and not isinstance(value, bool)
        for value in present
    ):
        return "quantitative"
    if all(isinstance(value, str) and _DATE_TEXT.match(value) for value in present):
        return "temporal"
    return "nominal"


def infer_field_types(data: list[dict]) -> dict[str, str]:
    """Типы всех колонок результата в порядке их появления."""
    columns: dict[str, None] = {}
    for record in data:
        columns.update(dict.fromkeys(record))
    return {column: infer_field_type([record.get(column) for record in data]) for column in columns}


def field_ref(column: str) -> str:
    """Экранировать имя колонки для поля Vega-Lite (точки и скобки)."""
    return _FIELD_ESCAPE.sub(r"\\\1", column)


def build_vega_spec(
    chart_type: str,
    result_table: str | None = None,
    db=None,
    data: list[dict] | None = None,
    titles: dict[str, str] | None = None,
    title: str | None = None,
) -> dict:
    """Собрать Vega-Lite спеку по типу графика и данным результата.

    Если data не передан, строки выгружаются из result_table через
    db.to_records(result_table, limit=SPEC_ROW_LIMIT).
    """
    if data is None:
        data = db.to_records(result_table, limit=SPEC_ROW_LIMIT) if db and result_table else []
    requested = chart_type if chart_type in CHART_TYPES else "bar"
    values = [{key: to_json_value(value) for key, value in record.items()} for record in data]
    types = infer_field_types(data)
    labels = {column: (titles or {}).get(column, column) for column in types}
    built, layout = _ENCODERS[requested](_Columns(types, labels))
    spec: dict[str, Any] = {
        "$schema": VEGA_LITE_SCHEMA,
        "usermeta": {
            "chart_type": built,
            "requested_type": requested,
            "result_table": result_table,
            "columns": [
                {"field": column, "title": labels[column], "type": kind}
                for column, kind in types.items()
            ],
        },
        "width": "container",
        "data": {"values": values},
        "mark": _MARKS[built],
        **layout,
    }
    if title:
        spec["title"] = title
    return spec


class _Columns:
    """Колонки результата, сгруппированные по типам каналов."""

    def __init__(self, types: dict[str, str], labels: dict[str, str]) -> None:
        self.types = types
        self.labels = labels
        self.numeric = [column for column, kind in types.items() if kind == "quantitative"]
        self.temporal = [column for column, kind in types.items() if kind == "temporal"]
        self.nominal = [column for column, kind in types.items() if kind == "nominal"]

    def channel(self, column: str, kind: str | None = None, **extra: Any) -> dict:
        return {
            "field": field_ref(column),
            "type": kind or self.types[column],
            "title": self.labels[column],
            **extra,
        }

    def tooltip(self) -> list[dict]:
        return [self.channel(column) for column in self.types]

    def category(self, exclude: tuple[str, ...] = ()) -> str | None:
        candidates = [*self.nominal, *self.temporal]
        return next((column for column in candidates if column not in exclude), None)

    def metric(self, exclude: tuple[str, ...] = ()) -> str | None:
        return next((column for column in self.numeric if column not in exclude), None)

    def order(self) -> tuple[str, str] | None:
        if self.temporal:
            return self.temporal[0], "temporal"
        named = [column for column in self.types if _ORDER_NAME.search(column)]
        if named:
            return named[0], "ordinal"
        if self.nominal:
            return self.nominal[0], "ordinal"
        if len(self.numeric) > 1:
            return self.numeric[0], "quantitative"
        return None


def _count(**extra: Any) -> dict:
    return {"aggregate": "count", "type": "quantitative", "title": COUNT_TITLE, **extra}


def _tip(*channels: dict) -> list[dict]:
    return [{key: channel[key] for key in _TOOLTIP_KEYS if key in channel} for channel in channels]


def _total(columns: _Columns, metric: str | None) -> dict:
    return columns.channel(metric, aggregate="sum") if metric else _count()


def _bar(columns: _Columns) -> tuple[str, dict]:
    category = columns.category()
    if category is None:
        return _histogram(columns) if columns.numeric else _table(columns)
    x_kind = "ordinal" if columns.types[category] == "temporal" else "nominal"
    x_channel = columns.channel(category, x_kind, sort=None)
    y_channel = _total(columns, columns.metric())
    encoding = {"x": x_channel, "y": y_channel, "tooltip": _tip(x_channel, y_channel)}
    return "bar", {"encoding": encoding}


def _line(columns: _Columns, chart_type: str = "line") -> tuple[str, dict]:
    order = columns.order()
    if order is None:
        return _bar(columns)
    x_column, x_kind = order
    metric = columns.metric(exclude=(x_column,))
    x_channel = columns.channel(x_column, x_kind)
    y_channel = _total(columns, metric)
    encoding = {"x": x_channel, "y": y_channel, "tooltip": _tip(x_channel, y_channel)}
    series = columns.category(exclude=(x_column,))
    if metric and series and columns.types[series] == "nominal":
        encoding["color"] = columns.channel(series)
        encoding["tooltip"].extend(_tip(encoding["color"]))
    return chart_type, {"encoding": encoding}


def _area(columns: _Columns) -> tuple[str, dict]:
    return _line(columns, "area")


def _histogram(columns: _Columns) -> tuple[str, dict]:
    metric = columns.metric()
    if metric is None:
        return _bar(columns)
    binned = columns.channel(metric, bin={"maxbins": 20})
    return "histogram", {
        "encoding": {"x": binned, "y": _count(), "tooltip": _tip(binned, _count())}
    }


def _scatter(columns: _Columns) -> tuple[str, dict]:
    if len(columns.numeric) < 2:
        return _bar(columns)
    x_column, y_column = columns.numeric[:2]
    encoding = {
        "x": columns.channel(x_column, scale={"zero": False}),
        "y": columns.channel(y_column, scale={"zero": False}),
        "tooltip": columns.tooltip(),
    }
    if columns.nominal:
        encoding["color"] = columns.channel(columns.nominal[0])
    return "scatter", {"encoding": encoding}


def _box(columns: _Columns) -> tuple[str, dict]:
    metric = columns.metric()
    if metric is None:
        return _bar(columns)
    category = columns.category()
    if category:
        x_channel = columns.channel(category, "nominal")
    else:
        x_channel = {"datum": columns.labels[metric], "type": "nominal", "title": None}
    encoding = {
        "x": x_channel,
        "y": columns.channel(metric, scale={"zero": False}),
        "tooltip": [columns.channel(metric)],
    }
    return "box", {"encoding": encoding}


def _heatmap(columns: _Columns) -> tuple[str, dict]:
    x_column = columns.category()
    y_column = columns.category(exclude=(x_column,)) if x_column else None
    if y_column is None:
        return _bar(columns)
    metric = columns.metric()
    color = (
        columns.channel(metric, aggregate="sum", scale={"scheme": "blues"})
        if metric
        else _count(scale={"scheme": "blues"})
    )
    encoding = {
        "x": columns.channel(x_column, "ordinal"),
        "y": columns.channel(y_column, "ordinal"),
        "color": color,
        "tooltip": _tip(columns.channel(x_column), columns.channel(y_column), color),
    }
    return "heatmap", {"encoding": encoding}


def _table(columns: _Columns) -> tuple[str, dict]:
    if not columns.types:
        return "table", {"encoding": {}}
    fields = [field_ref(column) for column in columns.types]
    encoding = {
        "x": {
            "field": COLUMN_FIELD,
            "type": "nominal",
            "title": None,
            "sort": fields,
            "axis": {"orient": "top", "labelAngle": 0},
        },
        "y": {"field": ROW_FIELD, "type": "ordinal", "title": None, "axis": None},
        "text": {"field": VALUE_FIELD, "type": "nominal"},
        "tooltip": [
            {"field": COLUMN_FIELD, "type": "nominal", "title": "Колонка"},
            {"field": VALUE_FIELD, "type": "nominal", "title": "Значение"},
        ],
    }
    transform = [
        {"window": [{"op": "row_number", "as": ROW_FIELD}]},
        {"fold": fields, "as": [COLUMN_FIELD, VALUE_FIELD]},
    ]
    return "table", {"transform": transform, "encoding": encoding}


_ENCODERS = {
    "bar": _bar,
    "line": _line,
    "area": _area,
    "scatter": _scatter,
    "histogram": _histogram,
    "box": _box,
    "heatmap": _heatmap,
    "table": _table,
}
