"""Выбор графика агентом: описание результатов для промпта и проверка выбора.

Вид графика и колонки осей называет модель — по готовым результатам, когда
их колонки и строки уже известны (плитка дашборда, ответ в чате). Здесь —
общая для обоих часть без LLM: как описать результат в промпте, подходит
ли выбор к данным (rejection — причина отказа для повтора) и топ-N строк
по limit.
"""

from app.core.tools.base import identifier
from app.core.viz.chart_selector import (
    MAX_BAR_CATEGORIES,
    fits,
    fits_columns,
    is_single_number,
)
from app.core.viz.vega_spec_builder import (
    detect_wide_matrix,
    heatmap_axis_candidates,
    infer_field_types,
    is_heatmap_axis,
    to_json_value,
)
from app.models.chart_choice import ChartPick

# Строк результата в промпте выбора графика.
SAMPLE_ROWS = 10


def describe_columns(records: list[dict]) -> str:
    """Колонки результата с типом канала и числом уникальных значений."""
    types = infer_field_types(records)
    return ", ".join(
        f"{column} ({kind}, уникальных {len({repr(row.get(column)) for row in records})})"
        for column, kind in types.items()
    )


def sample_rows(records: list[dict]) -> list[dict]:
    return [
        {key: to_json_value(value) for key, value in row.items()} for row in records[:SAMPLE_ROWS]
    ]


def rejection(choice: ChartPick, records: list[dict]) -> str | None:
    """Почему выбор не подходит к строкам результата; None — подходит."""
    if not records:
        return "результат пуст"
    types = infer_field_types(records)
    for name in (choice.x, choice.y, choice.color):
        if name is not None and name not in types:
            return f"колонки {name!r} нет в результате"
    if choice.chart_type == "kpi":
        return None if is_single_number(records) else "kpi — только когда результат одно число"
    data = limited(records, choice)
    if detect_wide_matrix(data, types) is not None:
        if choice.chart_type not in ("heatmap", "table"):
            return (
                f"{choice.chart_type}: таблица представляет собой матрицу (все строки и колонки — признаки/показатели); "
                f"для неё подходит heatmap или table"
            )
        if choice.chart_type == "heatmap":
            return None
    if choice.x is None and choice.y is None:
        if fits(choice.chart_type, data, types):
            return None
        return f"{choice.chart_type} не подходит к {len(records)} строкам с колонками {list(types)}"
    if choice.chart_type == "bar" and choice.x is not None:
        distinct = len({repr(row.get(choice.x)) for row in data})
        if distinct > MAX_BAR_CATEGORIES:
            return (
                f"bar: у {choice.x!r} {distinct} значений, больше {MAX_BAR_CATEGORIES} — "
                f"задай limit (топ-N по y) или другой вид"
            )
    if choice.chart_type == "heatmap":
        if detect_wide_matrix(data, types) is not None:
            return None
        if choice.x is not None and not is_heatmap_axis(data, types, choice.x):
            return (
                f"heatmap: x={choice.x!r} — числовая метрика; x и y должны быть "
                f"категориями (или датой и номером периода), а число — в color"
            )
        if choice.y is not None and not is_heatmap_axis(data, types, choice.y):
            if choice.color is None:
                candidates = heatmap_axis_candidates(data, types, exclude=(choice.x, choice.y))
                if candidates:
                    choice.color = choice.y
                    choice.y = candidates[0]
                else:
                    return (
                        f"heatmap: y={choice.y!r} — числовая метрика; x и y должны быть "
                        f"категориями (или датой и номером периода), а число — в color"
                    )
            else:
                return (
                    f"heatmap: y={choice.y!r} — числовая метрика; x и y должны быть "
                    f"категориями (или датой и номером периода), а число — в color"
                )
    if fits_columns(choice.chart_type, data, types, choice.x, choice.y, choice.color):
        return None
    named = ", ".join(
        f"{role}={column} ({types[column]})"
        for role, column in (("x", choice.x), ("y", choice.y))
        if column is not None
    )
    return f"{choice.chart_type} не подходит к {named} на {len(data)} строках"


def limited(records: list[dict], choice: ChartPick) -> list[dict]:
    """Строки, которые останутся после limit: топ-N по y (как top_rows, но в памяти)."""
    if choice.limit is None:
        return records
    if choice.y is not None:
        records = sorted(records, key=lambda row: _descending(row.get(choice.y)))
    return records[: choice.limit]


def top_rows(db, table: str, choice: ChartPick) -> str:
    """Таблица графика с учётом limit: новая temp_result_vN с топ-N по y."""
    if choice.limit is None:
        return table
    order = f" ORDER BY {identifier(choice.y)} DESC NULLS LAST" if choice.y else ""
    return db.save_step_result(
        table, f"SELECT * FROM {identifier(table)}{order} LIMIT {choice.limit}"
    )


def _descending(value: object) -> tuple[bool, float]:
    """Ключ сортировки по убыванию; не число (None) — в конец, как NULLS LAST."""
    try:
        return False, -float(value)
    except (TypeError, ValueError):
        return True, 0.0
