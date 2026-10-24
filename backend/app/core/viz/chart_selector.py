"""Выбор типа визуализации под вопрос.

Способ представления подбирается под вопрос и меняется по просьбе
пользователя. Логика: явный запрос вида -> таблица результата -> LLM.
"""

import math

from app.core.viz.vega_spec_builder import infer_field_types


def is_single_number(records: list[dict] | None) -> bool:
    if records is None or len(records) != 1:
        return False
    if list(infer_field_types(records).values()) != ["quantitative"]:
        return False
    return all(math.isfinite(value) for value in records[0].values())


def select_chart(
    question: str, result_table: str, requested: str = "auto", records: list[dict] | None = None
) -> str:
    """Подобрать тип графика: bar | line | scatter | histogram | kpi (или явный requested).

    Явный requested берётся как есть; результат из одной строки с одним
    конечным числом — kpi; иначе простые эвристики по ключевым словам вопроса.
    """
    if requested != "auto":
        return requested
    if is_single_number(records):
        return "kpi"
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
