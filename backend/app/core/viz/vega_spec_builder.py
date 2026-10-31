"""Построитель Vega-Lite спецификаций из таблицы результата.

Один инсайт -> JSON-конфиг (Vega-Lite v5), который читается фронтендом
(react-vega) без дополнительной серверной отрисовки. Данные берутся из
temp_result_vN (или передаются готовыми записями), типы каналов
определяются по значениям: даты -> temporal, числа -> quantitative,
остальное -> nominal. Если данных не хватает для запрошенного вида,
спека строится в ближайшем подходящем виде (см. usermeta.chart_type).

Колонки осей можно назвать явно (x, y, color — выбор агента, см.
viz/chart_choice.py): тогда график строится по ним, а не по первой
категории и первому числу. Целочисленный ключ на оси x столбцов
(store_id) остаётся категорией.
"""

import datetime as dt
import math
import re
from decimal import Decimal
from typing import Any

VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"
SPEC_ROW_LIMIT = 500
CHART_TYPES = ("bar", "line", "area", "scatter", "histogram", "box", "heatmap", "table", "kpi")
COUNT_TITLE = "Количество"
KPI_FORMAT = ",.2~f"
ROW_FIELD = "_row"
COLUMN_FIELD = "_column"
VALUE_FIELD = "_value"
# С какого числа категорий столбцы лежат: длинные подписи (регионы) не налезают.
HORIZONTAL_BARS = 12
# Высота одной лежащей полосы, px: график растёт с числом категорий.
BAR_STEP = 18

_DATE_TEXT = re.compile(r"^\d{4}-\d{2}(-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?)?$")
_ORDER_NAME = re.compile(
    r"(year|month|quarter|week|day|date|period|time|hour|номер|год|месяц|квартал|недел|день|дата|период|час)",
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
    "kpi": {"type": "text", "fontSize": 48},
}


def column_aliases(passport: dict) -> dict[str, str]:
    """Читаемые названия колонок из паспорта данных (ProfileMemory)."""
    titles: dict[str, str] = {}
    for key in ("glossary", "titles"):
        mapping = passport.get(key)
        if not isinstance(mapping, dict):
            continue
        for column, alias in mapping.items():
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


def ordered_column(types: dict[str, str]) -> str | None:
    temporal = [column for column, kind in types.items() if kind == "temporal"]
    if temporal:
        return temporal[0]
    return next((column for column in types if _ORDER_NAME.search(column)), None)


def _temporal_step(values: list[Any], column_name: str = "") -> tuple[str, str]:
    """Определить timeUnit и axis.format для temporal-колонки на оси heatmap.

    Возвращает (timeUnit, axis_format):
    - ('yearmonth', '%m.%Y') для месяцев
    - ('yearmonthdate', '%d.%m.%Y') для дней и недель
    """
    present = [v for v in values if v is not None]
    if not present:
        return "yearmonthdate", "%d.%m.%Y"

    if all(isinstance(v, str) and re.match(r"^\d{4}-\d{2}$", v) for v in present):
        return "yearmonth", "%m.%Y"

    parsed_dates: list[dt.date] = []
    for v in present:
        if isinstance(v, dt.datetime):
            parsed_dates.append(v.date())
        elif isinstance(v, dt.date):
            parsed_dates.append(v)
        elif isinstance(v, str):
            try:
                parsed_dates.append(dt.date.fromisoformat(v[:10]))
            except ValueError:
                pass

    if not parsed_dates or any(d.day != 1 for d in parsed_dates):
        return "yearmonthdate", "%d.%m.%Y"

    if re.search(r"(month|месяц)", column_name, re.IGNORECASE):
        return "yearmonth", "%m.%Y"

    unique_dates = sorted(set(parsed_dates))
    if len(unique_dates) >= 2:
        return "yearmonth", "%m.%Y"

    return "yearmonthdate", "%d.%m.%Y"


def is_integer_value(v: object) -> bool:
    if isinstance(v, bool) or v is None:
        return False
    return isinstance(v, int)


def is_integer_column(records: list[dict], column: str) -> bool:
    values = [r.get(column) for r in records if r.get(column) is not None]
    return bool(values) and all(is_integer_value(v) for v in values)


def is_fractional_column(records: list[dict], column: str) -> bool:
    values = [r.get(column) for r in records if r.get(column) is not None]
    return any(isinstance(v, float | Decimal) for v in values)


def is_heatmap_axis(records: list[dict], types: dict[str, str], column: str | None) -> bool:
    if column is None or column not in types:
        return False
    kind = types[column]
    if records:
        values = [r.get(column) for r in records if r.get(column) is not None]
        if values:
            # Длинные описательные тексты (комментарии, сводки значений) не могут быть осью
            str_vals = [v for v in values if isinstance(v, str)]
            if str_vals and (
                any(len(s) > 50 for s in str_vals)
                or sum(len(s) for s in str_vals) / len(str_vals) > 30
            ):
                return False
            if len(records) >= 5:
                # Если колонка уникальна для каждой строки, это первичный ключ таблицы,
                # а не ось 2D-сетки тепловой карты (на каждый X будет ровно одна точка).
                distinct = len({repr(v) for v in values})
                if distinct == len(records):
                    return False
    if kind in ("nominal", "temporal"):
        return True
    if kind == "quantitative" and is_integer_column(records, column):
        int_vals = [r.get(column) for r in records if r.get(column) is not None]
        distinct = len({repr(r.get(column)) for r in records})
        if distinct <= 50:
            if _ORDER_NAME.search(column):
                return True
            max_val = max(int_vals) if int_vals else 0
            min_val = min(int_vals) if int_vals else 0
            if max_val <= 50 and min_val >= 0:
                return True
    return False


def heatmap_axis_candidates(
    records: list[dict], types: dict[str, str], exclude: tuple[str, ...] = ()
) -> list[str]:
    nominal = [
        c
        for c, k in types.items()
        if k == "nominal" and c not in exclude and is_heatmap_axis(records, types, c)
    ]
    temporal = [
        c
        for c, k in types.items()
        if k == "temporal" and c not in exclude and is_heatmap_axis(records, types, c)
    ]
    categories = [*nominal, *temporal]
    int_cols = [
        c
        for c, k in types.items()
        if k == "quantitative" and c not in exclude and is_heatmap_axis(records, types, c)
    ]
    order_ints = [c for c in int_cols if _ORDER_NAME.search(c)]
    other_ints = [c for c in int_cols if not _ORDER_NAME.search(c)]
    return [*categories, *order_ints, *other_ints]


def heatmap_metric_candidate(
    records: list[dict], types: dict[str, str], exclude: tuple[str, ...] = ()
) -> str | None:
    numerics = [c for c, k in types.items() if k == "quantitative" and c not in exclude]
    if not numerics:
        return None
    fractional = [c for c in numerics if is_fractional_column(records, c)]
    if fractional:
        return fractional[0]
    large = [c for c in numerics if len({repr(r.get(c)) for r in records}) > 50]
    if large:
        return large[0]
    other = [c for c in numerics if not _ORDER_NAME.search(c)]
    if other:
        return other[0]
    return numerics[0]


def detect_wide_matrix(
    records: list[dict],
    types: dict[str, str],
) -> tuple[list[str], str | None] | None:
    """Определить, представляют ли строки широкую матрицу (например, корреляционную).

    Возвращает (quant_cols, row_col) или None:
    - quant_cols: список числовых колонок (столбцы матрицы);
    - row_col: имя колонки с названиями строк (если есть) или None.
    """
    if not records or len(records) < 2:
        return None
    quant_cols = [c for c, kind in types.items() if kind == "quantitative"]
    if len(quant_cols) < 2:
        return None
    non_quant = [c for c, kind in types.items() if kind != "quantitative"]
    if len(non_quant) == 0:
        if len(records) == len(quant_cols):
            return quant_cols, None
        return None
    if len(non_quant) == 1:
        row_col = non_quant[0]
        if types.get(row_col) == "temporal":
            return None
        distinct_rows = len({repr(r.get(row_col)) for r in records})
        if distinct_rows == len(records):
            row_vals = {str(r.get(row_col)) for r in records if r.get(row_col) is not None}
            if row_vals.issubset(set(quant_cols)) or (
                distinct_rows == len(quant_cols) and bool(row_vals.intersection(set(quant_cols)))
            ):
                return quant_cols, row_col
            if len(quant_cols) >= 3 and not any(_ORDER_NAME.search(c) for c in quant_cols):
                return quant_cols, row_col
    return None


def is_correlation_matrix(
    records: list[dict],
    quant_cols: list[str],
    row_col: str | None = None,
) -> bool:
    """Проверить, является ли матрица корреляционной (значения в [-1, 1], диагональ ~1.0)."""
    if len(records) != len(quant_cols):
        return False
    diag_ones = 0
    total_checked = 0
    for i, r in enumerate(records):
        row_name = str(r.get(row_col)) if row_col is not None else quant_cols[i]
        for j, col in enumerate(quant_cols):
            val = r.get(col)
            if val is not None:
                if not isinstance(val, int | float | Decimal):
                    return False
                fval = float(val)
                if math.isnan(fval) or math.isinf(fval):
                    continue
                if not (-1.01 <= fval <= 1.01):
                    return False
                total_checked += 1
                if (col == row_name or i == j) and abs(fval - 1.0) < 0.02:
                    diag_ones += 1
    return total_checked > 0 and diag_ones >= max(1, len(quant_cols) - 1)


def _axis_channel(columns: "_Columns", col: str) -> dict:
    if columns.types.get(col) == "temporal":
        values = [r.get(col) for r in columns.data if r.get(col) is not None]
        unit, fmt = _temporal_step(values, col)
        return columns.channel(col, "ordinal", timeUnit=unit, axis={"format": fmt})
    return columns.channel(col, "ordinal")


def chart_title(data: list[dict], titles: dict[str, str] | None = None) -> str | None:
    types = infer_field_types(data)
    columns = _Columns(
        types, {column: (titles or {}).get(column, column) for column in types}, data=data
    )
    metric = columns.metric()
    category = columns.category(exclude=(metric,) if metric else ())
    metric_label = columns.labels[metric] if metric else COUNT_TITLE
    if category is None:
        return metric_label if metric else None
    category_label = columns.labels[category]
    return f"{metric_label} по {category_label[:1].lower()}{category_label[1:]}"


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
    x: str | None = None,
    y: str | None = None,
    color: str | None = None,
) -> dict:
    """Собрать Vega-Lite спеку по типу графика и данным результата.

    Если data не передан, строки выгружаются из result_table через
    db.to_records(result_table, limit=SPEC_ROW_LIMIT). x, y, color —
    колонки осей и разбивки цветом; колонки, которой нет в данных, нет и
    в выборе.
    """
    if data is None:
        data = db.to_records(result_table, limit=SPEC_ROW_LIMIT) if db and result_table else []
    requested = chart_type if chart_type in CHART_TYPES else "bar"
    values = [{key: to_json_value(value) for key, value in record.items()} for record in data]
    types = infer_field_types(data)
    labels = {column: (titles or {}).get(column, column) for column in types}
    picked = {
        role: column
        for role, column in (("x", x), ("y", y), ("color", color))
        if column is not None and column in types
    }
    distinct = {column: len({repr(record.get(column)) for record in data}) for column in types}
    built, layout = _ENCODERS[requested](_Columns(types, labels, picked, distinct, data=data))
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
        "data": layout.get("data") or {"values": values},
        "mark": _MARKS[built],
        **{k: v for k, v in layout.items() if k not in ("data", "usermeta_fields")},
    }
    # Выбор колонок переживает пересчёт плитки (dashboards/recalculate.py).
    spec["usermeta"].update(picked)
    if "usermeta_fields" in layout:
        spec["usermeta"].update(layout["usermeta_fields"])
    elif built == "heatmap":
        enc = layout.get("encoding", {})
        spec["usermeta"]["x"] = enc.get("x", {}).get("field")
        spec["usermeta"]["y"] = enc.get("y", {}).get("field")
        if "field" in enc.get("color", {}):
            spec["usermeta"]["color"] = enc["color"]["field"]
        elif "color" in spec["usermeta"]:
            del spec["usermeta"]["color"]
    elif requested == "heatmap" and built == "bar":
        enc = layout.get("encoding", {})
        spec["usermeta"]["x"] = enc.get("x", {}).get("field")
        spec["usermeta"]["y"] = enc.get("y", {}).get("field")
    if title:
        spec["title"] = title
    return spec


class _Columns:
    """Колонки результата, сгруппированные по типам каналов.

    picked — колонки, названные явно (x, y, color). Если названа хоть одна,
    разбивка цветом — только по color: без него график одной серией.
    """

    def __init__(
        self,
        types: dict[str, str],
        labels: dict[str, str],
        picked: dict[str, str] | None = None,
        distinct: dict[str, int] | None = None,
        data: list[dict] | None = None,
    ) -> None:
        self.types = types
        self.labels = labels
        self.picked = picked or {}
        self.distinct = distinct or {}
        self.data = data or []
        self.numeric = [column for column, kind in types.items() if kind == "quantitative"]
        self.temporal = [column for column, kind in types.items() if kind == "temporal"]
        self.nominal = [column for column, kind in types.items() if kind == "nominal"]

    def is_axis_candidate(self, column: str | None) -> bool:
        return is_heatmap_axis(self.data, self.types, column)

    def axis_candidates(self, exclude: tuple[str, ...] = ()) -> list[str]:
        return heatmap_axis_candidates(self.data, self.types, exclude)

    def heatmap_metric(self, exclude: tuple[str, ...] = ()) -> str | None:
        return heatmap_metric_candidate(self.data, self.types, exclude)

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

    def pick(self, role: str, exclude: tuple[str, ...] = ()) -> str | None:
        column = self.picked.get(role)
        return column if column not in exclude else None

    def numeric_pick(self, *roles: str, exclude: tuple[str, ...] = ()) -> str | None:
        """Первая из названных колонок ролей roles, если она числовая."""
        for role in roles:
            column = self.pick(role, exclude)
            if column is not None and self.types[column] == "quantitative":
                return column
        return None

    def value(self, exclude: tuple[str, ...] = ()) -> str | None:
        """Число для оси y: названное или первое числовое."""
        return self.numeric_pick("y", exclude=exclude) or self.metric(exclude)

    def picked_order(self) -> tuple[str, str] | None:
        x_column = self.pick("x")
        if x_column is None:
            return None
        kind = self.types[x_column]
        # Год или номер месяца числом — порядок, а не величина («2 024»).
        if kind == "temporal" or (kind == "quantitative" and not _ORDER_NAME.search(x_column)):
            return x_column, kind
        return x_column, "ordinal"

    def order(self) -> tuple[str, str] | None:
        ordered = ordered_column(self.types)
        if ordered is not None:
            return ordered, "temporal" if self.types[ordered] == "temporal" else "ordinal"
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
    category = columns.pick("x") or columns.category()
    if category is None:
        return _histogram(columns) if columns.numeric else _table(columns)
    x_kind = "ordinal" if columns.types[category] == "temporal" else "nominal"
    metric = columns.value(exclude=(category,))
    value_channel = _total(columns, metric)
    # Много категорий — полосы лёжа, по убыванию значения; даты — всегда по оси x.
    lying = x_kind == "nominal" and columns.distinct.get(category, 0) > HORIZONTAL_BARS
    category_channel = columns.channel(category, x_kind, sort="-x" if lying else None)
    across, along = ("y", "x") if lying else ("x", "y")
    encoding = {
        across: category_channel,
        along: value_channel,
        "tooltip": _tip(category_channel, value_channel),
    }
    series = columns.pick("color", exclude=(category, metric))
    if series is not None:
        # Столбцы серий рядом, а не стопкой: сравнивают значения, а не суммы.
        encoding["color"] = columns.channel(series, "nominal")
        encoding[f"{across}Offset"] = {"field": field_ref(series), "type": "nominal"}
        encoding["tooltip"] = _tip(category_channel, encoding["color"], value_channel)
    layout: dict[str, Any] = {"encoding": encoding}
    if lying:
        layout["height"] = {"step": BAR_STEP}
    return "bar", layout


def _line(columns: _Columns, chart_type: str = "line") -> tuple[str, dict]:
    order = columns.picked_order() or columns.order()
    if order is None:
        return _bar(columns)
    x_column, x_kind = order
    metric = columns.value(exclude=(x_column,))
    x_channel = columns.channel(x_column, x_kind)
    y_channel = _total(columns, metric)
    encoding = {"x": x_channel, "y": y_channel, "tooltip": _tip(x_channel, y_channel)}
    if columns.picked:
        series = columns.pick("color", exclude=(x_column, metric))
    else:
        series = columns.category(exclude=(x_column,))
        if not (metric and series and columns.types[series] == "nominal"):
            series = None
    if series is not None:
        encoding["color"] = columns.channel(series, "nominal")
        encoding["tooltip"].extend(_tip(encoding["color"]))
    return chart_type, {"encoding": encoding}


def _area(columns: _Columns) -> tuple[str, dict]:
    return _line(columns, "area")


def _histogram(columns: _Columns) -> tuple[str, dict]:
    metric = columns.numeric_pick("y", "x") or columns.metric()
    if metric is None:
        return _bar(columns)
    binned = columns.channel(metric, bin={"maxbins": 20})
    return "histogram", {
        "encoding": {"x": binned, "y": _count(), "tooltip": _tip(binned, _count())}
    }


def _scatter(columns: _Columns) -> tuple[str, dict]:
    if len(columns.numeric) < 2:
        return _bar(columns)
    x_column = columns.numeric_pick("x")
    y_column = columns.numeric_pick("y", exclude=(x_column,)) if x_column else None
    if y_column is None:
        x_column, y_column = columns.numeric[:2]
    encoding = {
        "x": columns.channel(x_column, scale={"zero": False}),
        "y": columns.channel(y_column, scale={"zero": False}),
        "tooltip": columns.tooltip(),
    }
    if columns.picked:
        series = columns.pick("color", exclude=(x_column, y_column))
    else:
        series = columns.nominal[0] if columns.nominal else None
    if series is not None:
        encoding["color"] = columns.channel(series, "nominal")
    return "scatter", {"encoding": encoding}


def _box(columns: _Columns) -> tuple[str, dict]:
    metric = columns.value()
    if metric is None:
        return _bar(columns)
    category = columns.pick("x", exclude=(metric,)) if columns.picked else columns.category()
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
    explicit_valid_axes = (
        bool(columns.picked.get("x"))
        and bool(columns.picked.get("y"))
        and columns.is_axis_candidate(columns.picked["x"])
        and columns.is_axis_candidate(columns.picked["y"])
    )
    matrix_info = None if explicit_valid_axes else detect_wide_matrix(columns.data, columns.types)
    if matrix_info is not None:
        quant_cols, row_col = matrix_info
        is_corr = is_correlation_matrix(columns.data, quant_cols, row_col)
        unpivoted: list[dict[str, Any]] = []
        for i, r in enumerate(columns.data):
            row_name = str(r.get(row_col)) if row_col is not None else quant_cols[i]
            for col in quant_cols:
                val = r.get(col)
                unpivoted.append(
                    {
                        "x": col,
                        "y": row_name,
                        "value": to_json_value(val),
                    }
                )

        color_scale = {"scheme": "redblue", "domain": [-1, 1]} if is_corr else {"scheme": "blues"}
        color_title = "Корреляция" if is_corr else "Значение"
        y_domain = (
            quant_cols
            if is_corr
            else [str(r.get(row_col)) for r in columns.data if row_col is not None]
        )
        tooltip = [
            {"field": "x", "type": "nominal", "title": "Признак 1" if is_corr else "Колонка"},
            {"field": "y", "type": "nominal", "title": "Признак 2" if is_corr else "Строка"},
            {
                "field": "value",
                "type": "quantitative",
                "title": color_title,
                **({"format": ".2f"} if is_corr else {}),
            },
        ]
        encoding = {
            "x": {
                "field": "x",
                "type": "nominal",
                "title": None,
                "scale": {"domain": quant_cols},
                "axis": {"labelAngle": -45},
            },
            "y": {
                "field": "y",
                "type": "nominal",
                "title": None,
                "scale": {"domain": y_domain},
            },
            "color": {
                "field": "value",
                "type": "quantitative",
                "title": color_title,
                "scale": color_scale,
            },
            "tooltip": tooltip,
        }
        return "heatmap", {
            "encoding": encoding,
            "data": {"values": unpivoted},
            "usermeta_fields": {"x": "x", "y": "y", "color": "value"},
        }

    # У тепловой карты y — вторая категория, а число — в color.
    x_column = columns.pick("x")
    y_column = columns.pick("y")
    color_column = columns.numeric_pick("color")

    if y_column is not None and not columns.is_axis_candidate(y_column):
        if color_column is None:
            color_column = y_column
            y_column = None
            replacements = columns.axis_candidates(exclude=(x_column, color_column))
            if replacements:
                y_column = replacements[0]
        else:
            y_column = None

    if x_column is not None and not columns.is_axis_candidate(x_column):
        x_column = None

    tentative_metric = color_column or columns.heatmap_metric(
        exclude=tuple(c for c in (x_column, y_column) if c is not None)
    )
    exclude_for_axes = tuple(c for c in (tentative_metric,) if c is not None)
    candidates = columns.axis_candidates(exclude=exclude_for_axes)

    if x_column is None:
        remaining = [c for c in candidates if c != y_column]
        if remaining:
            x_column = remaining[0]

    if y_column is None:
        remaining = [c for c in candidates if c != x_column]
        if remaining:
            y_column = remaining[0]

    if x_column is None or y_column is None or x_column == y_column:
        return _bar(columns)

    if color_column is None:
        color_column = columns.heatmap_metric(exclude=(x_column, y_column))

    x_channel = _axis_channel(columns, x_column)
    y_channel = _axis_channel(columns, y_column)
    color = (
        columns.channel(color_column, aggregate="sum", scale={"scheme": "blues"})
        if color_column
        else _count(scale={"scheme": "blues"})
    )
    x_tip = columns.channel(x_column)
    y_tip = columns.channel(y_column)
    if columns.types.get(x_column) == "temporal":
        x_tip["timeUnit"] = x_channel.get("timeUnit")
        x_tip["format"] = x_channel.get("axis", {}).get("format")
    if columns.types.get(y_column) == "temporal":
        y_tip["timeUnit"] = y_channel.get("timeUnit")
        y_tip["format"] = y_channel.get("axis", {}).get("format")

    encoding = {
        "x": x_channel,
        "y": y_channel,
        "color": color,
        "tooltip": _tip(x_tip, y_tip, color),
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


def _kpi(columns: _Columns) -> tuple[str, dict]:
    metric = columns.value()
    if metric is None:
        return _table(columns)
    text_channel = columns.channel(metric, format=KPI_FORMAT)
    return "kpi", {
        "title": columns.labels[metric],
        "transform": [
            {"window": [{"op": "row_number", "as": ROW_FIELD}]},
            {"filter": f"datum.{ROW_FIELD} == 1"},
        ],
        "encoding": {"text": text_channel, "tooltip": _tip(text_channel)},
    }


_ENCODERS = {
    "bar": _bar,
    "line": _line,
    "area": _area,
    "scatter": _scatter,
    "histogram": _histogram,
    "box": _box,
    "heatmap": _heatmap,
    "table": _table,
    "kpi": _kpi,
}
