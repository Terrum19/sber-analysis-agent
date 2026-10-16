"""Морфинг графиков: смена вида БЕЗ нового вызова LLM.

Требование: «А теперь покажи то же самое в другом виде» — берём
сохранённый результат (state cache) и перестраиваем спеку в другой
тип. Быстро, дёшево, не нагружает локальную модель.
"""

from app.core.viz.vega_spec_builder import build_vega_spec

MORPH_MAP = {
    "bar": ["line", "histogram", "table"],
    "line": ["bar", "area", "table"],
    "histogram": ["bar", "box"],
    "scatter": ["bar", "line"],
}


def morph(
    current_type: str,
    to_type: str,
    data: list[dict],
    result_table: str | None = None,
) -> dict:
    """Перестроить спеку в другой вид из тех же данных.

    Аргументы:
        current_type: текущий тип графика
        to_type: целевой тип графика
        data: готовые данные (список словарей)
        result_table: имя источника данных (для usermeta, опционально)
    """
    allowed = MORPH_MAP.get(current_type, [])
    target = to_type if to_type in allowed else (allowed[0] if allowed else current_type)
    return build_vega_spec(target, result_table=result_table, data=data)
