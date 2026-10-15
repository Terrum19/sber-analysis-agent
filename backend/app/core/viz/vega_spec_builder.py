"""Построитель Vega-Lite специк из таблицы результата.

Один инсайт -> JSON-конфиг (Vega-Lite), который читается фронтендом
(react-vega) без дополнительной серверной отрисовки.
"""


def build_vega_spec(chart_type: str, result_table: str, data: list[dict] | None = None) -> dict:
    """Собрать Vega-Lite спеку по типу графика и данным.

    TODO(round-2): вытянуть data из DuckDB по result_table, определить
    категиальные/числовые каналы автоматически из схемы таблицы.
    """
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": {"histogram": "bar", "bar": "bar", "line": "line", "scatter": "point"}.get(
            chart_type, "bar"
        ),
        "data": {"values": data or []},
        "encoding": {},
    }
