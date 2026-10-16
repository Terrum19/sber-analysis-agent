"""Построитель Vega-Lite спецификации из таблицы результата.

Один инсайт -> JSON-конфиг (Vega-Lite), который читается фронтендом
(react-vega) без дополнительной серверной отрисовки.

Модуль не подключается к БД сам — данные передаются вызывающей стороной
(оркестратором или морфингом) уже готовыми списком словарей.
"""

from datetime import date, datetime
from typing import Any


def _detect_column_types(data: list[dict]) -> dict[str, str]:
    """Авто-определение типа каждой колонки по первому непустому ряду.

    Правила:
      - int / float                        -> quantitative
      - datetime.date / datetime.datetime  -> temporal
      - str / bool / None                  -> nominal
    """
    if not data:
        return {}

    sample: dict[str, Any] = {}
    for row in data:
        for key, value in row.items():
            if value is not None:
                sample[key] = value
        if len(sample) == len(data[0]):
            break

    types: dict[str, str] = {}
    for key, value in sample.items():
        if isinstance(value, bool):
            types[key] = "nominal"
        elif isinstance(value, (int, float)):
            types[key] = "quantitative"
        elif isinstance(value, (date, datetime)):
            types[key] = "temporal"
        else:
            types[key] = "nominal"
    return types


def _first_by_type(column_types: dict[str, str], wanted: str) -> str | None:
    """Вернуть первую колонку заданного типа, или None."""
    for col, ctype in column_types.items():
        if ctype == wanted:
            return col
    return None


def _all_by_type(column_types: dict[str, str], wanted: str) -> list[str]:
    """Вернуть все колонки заданного типа в порядке следования."""
    return [col for col, ctype in column_types.items() if ctype == wanted]


def _build_encoding(
    chart_type: str,
    column_types: dict[str, str],
    data: list[dict],
) -> dict[str, Any]:
    """Построить блок encoding для заданного типа графика."""
    quant_cols = _all_by_type(column_types, "quantitative")
    temp_cols = _all_by_type(column_types, "temporal")
    nominal_cols = _all_by_type(column_types, "nominal")

    encoding: dict[str, Any] = {}

    def x(col: str, ctype: str) -> None:
        encoding["x"] = {"field": col, "type": ctype}

    def y(col: str, ctype: str) -> None:
        encoding["y"] = {"field": col, "type": ctype}

    def color(col: str, ctype: str) -> None:
        encoding["color"] = {"field": col, "type": ctype}

    if chart_type == "bar":
        x_col = _first_by_type(column_types, "nominal") or _first_by_type(column_types, "quantitative")
        y_col = _first_by_type(column_types, "quantitative")
        if x_col:
            x(x_col, "nominal" if x_col in nominal_cols else "quantitative")
        if y_col:
            y(y_col, "quantitative")
    elif chart_type == "line":
        x_col = _first_by_type(column_types, "temporal") or _first_by_type(column_types, "nominal")
        y_col = _first_by_type(column_types, "quantitative")
        if x_col:
            x(x_col, "temporal" if x_col in temp_cols else "nominal")
        if y_col:
            y(y_col, "quantitative")
    elif chart_type == "area":
        x_col = _first_by_type(column_types, "temporal") or _first_by_type(column_types, "nominal")
        y_col = _first_by_type(column_types, "quantitative")
        if x_col:
            x(x_col, "temporal" if x_col in temp_cols else "nominal")
        if y_col:
            y(y_col, "quantitative")
    elif chart_type == "scatter":
        if len(quant_cols) >= 2:
            x(quant_cols[0], "quantitative")
            y(quant_cols[1], "quantitative")
        elif quant_cols:
            x(quant_cols[0], "quantitative")
    elif chart_type == "histogram":
        x_col = _first_by_type(column_types, "quantitative")
        if x_col:
            encoding["x"] = {"field": x_col, "type": "quantitative", "bin": True}
            encoding["y"] = {"aggregate": "count", "type": "quantitative"}
    elif chart_type == "box":
        x_col = _first_by_type(column_types, "nominal") or _first_by_type(column_types, "quantitative")
        y_col = _first_by_type(column_types, "quantitative")
        if x_col:
            x(x_col, "nominal")
        if y_col:
            y(y_col, "quantitative")
    elif chart_type == "heatmap":
        x_col = nominal_cols[0] if nominal_cols else quant_cols[0] if quant_cols else None
        y_col = nominal_cols[1] if len(nominal_cols) > 1 else quant_cols[1] if len(quant_cols) > 1 else None
        color_col = quant_cols[0] if quant_cols else None
        if x_col:
            x(x_col, "nominal")
        if y_col:
            y(y_col, "nominal")
        if color_col:
            color(color_col, "quantitative")
    elif chart_type == "table":
        pass

    return encoding


def build_vega_spec(
    chart_type: str,
    result_table: str | None = None,
    data: list[dict] | None = None,
) -> dict:
    """Собрать Vega-Lite спеку по типу графика и данным.

    Аргументы:
        chart_type: bar | line | area | scatter | histogram | box | heatmap | table
        result_table: имя источника данных (для usermeta, опционально)
        data: готовые данные в формате списка словарей

    Возвращает:
        dict — Vega-Lite спецификация, готовая к отправке фронтенду.
    """
    rows: list[dict] = list(data or [])
    column_types: dict[str, str] = _detect_column_types(rows)

    mark_map = {
        "bar": "bar",
        "line": "line",
        "area": "area",
        "scatter": "point",
        "histogram": "bar",
        "box": "boxplot",
        "heatmap": "rect",
        "table": "table",
    }
    mark = mark_map.get(chart_type, "bar")

    spec: dict[str, Any] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "description": f"{chart_type} chart built from {result_table or 'data'}",
        "mark": mark,
        "data": {"values": rows},
    }

    if rows and chart_type != "table":
        spec["encoding"] = _build_encoding(chart_type, column_types, rows)
    elif chart_type == "table":
        spec["encoding"] = {}
    else:
        spec["encoding"] = {}

    spec["usermeta"] = {
        "chart_type": chart_type,
        "source_table": result_table,
        "columns": column_types,
        "row_count": len(rows),
        "encoding_channels": list(spec.get("encoding", {}).keys()),
    }

    return spec
