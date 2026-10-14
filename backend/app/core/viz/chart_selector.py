"""Выбор типа визуализации под вопрос.

Способ представления подбирается под вопрос и меняется по просьбе
пользователя. Логика: явный запрос вида -> таблица результата -> LLM.
"""


def select_chart(question: str, result_table: str, requested: str = "auto") -> str:
    """Подобрать тип графика: bar | line | scatter | histogram | table.

    TODO(round-2): если requested != "auto" — уважать явный выбор;
    иначе простые эвристики по ключевым словам вопроса.
    """
    if requested != "auto":
        return requested
    q = question.lower()
    if "распредел" in q or "гистограмм" in q:
        return "histogram"
    if "динамик" in q or "во времени" in q or "по годам" in q or "по месяцам" in q:
        return "line"
    if "сравн" in q or "по категори" in q:
        return "bar"
    if "связ" in q or "корреляц" in q:
        return "scatter"
    return "bar"
