"""Выбор типа визуализации под вопрос.

Способ представления подбирается под вопрос и меняется по просьбе
пользователя. Логика: явный запрос вида -> таблица результата -> LLM.
"""

import math

from app.core.viz.vega_spec_builder import infer_field_types, ordered_column

NO_CHART = "none"
MAX_BAR_CATEGORIES = 30
MIN_LINE_POINTS = 3
MIN_HISTOGRAM_VALUES = 20


def is_single_number(records: list[dict] | None) -> bool:
    if records is None or len(records) != 1:
        return False
    if list(infer_field_types(records).values()) != ["quantitative"]:
        return False
    return all(math.isfinite(value) for value in records[0].values())


def fits(chart_type: str, records: list[dict], types: dict[str, str]) -> bool:
    numeric = [column for column, kind in types.items() if kind == "quantitative"]
    categories = [
        *[column for column, kind in types.items() if kind == "nominal"],
        *[column for column, kind in types.items() if kind == "temporal"],
    ]
    if chart_type in ("line", "area"):
        return len(records) >= MIN_LINE_POINTS and ordered_column(types) is not None
    if chart_type == "scatter":
        return len(numeric) >= 2
    if chart_type == "histogram":
        return len(numeric) == 1 and _present(records, numeric[0]) >= MIN_HISTOGRAM_VALUES
    if chart_type == "box":
        return bool(numeric)
    if chart_type == "bar":
        return bool(categories) and _distinct(records, categories[0]) <= MAX_BAR_CATEGORIES
    if chart_type == "heatmap":
        return len(categories) >= 2
    if chart_type == "kpi":
        return is_single_number(records)
    return chart_type == "table"


def select_chart(
    question: str, result_table: str, requested: str = "auto", records: list[dict] | None = None
) -> str:
    """Подобрать тип графика: bar | line | scatter | histogram | kpi | table (или none).

    Результат из одной строки — kpi (одно конечное число) или table. Явный
    requested или эвристика по ключевым словам вопроса проверяются по
    records через fits; не подошло — table. Без records проверки нет.
    """
    if requested == NO_CHART:
        return NO_CHART
    if records is not None and len(records) <= 1:
        return "kpi" if is_single_number(records) else "table"
    chart_type = requested if requested != "auto" else _by_question(question)
    if records is None or fits(chart_type, records, infer_field_types(records)):
        return chart_type
    return "table"


def _by_question(question: str) -> str:
    text = question.lower()
    if "распредел" in text or "гистограмм" in text:
        return "histogram"
    if "динамик" in text or "во времени" in text or "по годам" in text or "по месяцам" in text:
        return "line"
    if "сравн" in text or "по категори" in text:
        return "bar"
    if "связ" in text or "корреляц" in text:
        return "scatter"
    return "bar"


def _present(records: list[dict], column: str) -> int:
    return sum(record.get(column) is not None for record in records)


def _distinct(records: list[dict], column: str) -> int:
    return len({repr(record.get(column)) for record in records})
