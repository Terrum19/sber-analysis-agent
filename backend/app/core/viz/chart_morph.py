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


def morph(current_type: str, to_type: str, data: list[dict]) -> dict:
    """Перестроить спеку в другой вид из тех же данных."""
    allowed = MORPH_MAP.get(current_type, [])
    target = to_type if to_type in allowed else (allowed[0] if allowed else current_type)
    return build_vega_spec(target, data)
