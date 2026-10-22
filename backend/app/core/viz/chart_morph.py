"""Морфинг графиков: смена вида БЕЗ нового вызова LLM.

Требование: «А теперь покажи то же самое в другом виде» — берём
сохранённый результат (state cache) и перестраиваем спеку в другой
тип. Быстро, дёшево, не нагружает локальную модель.
"""

import re

from app.core.viz.vega_spec_builder import CHART_TYPES, build_vega_spec

AUTO = "auto"

MORPH_MAP = {
    "bar": ["line", "table", "histogram", "heatmap"],
    "line": ["bar", "area", "table"],
    "area": ["line", "bar", "table"],
    "histogram": ["box", "bar", "table"],
    "box": ["histogram", "bar", "table"],
    "scatter": ["bar", "line", "table"],
    "heatmap": ["bar", "table"],
    "table": ["bar", "line"],
    "kpi": ["bar", "table"],
}

_TYPE_WORDS = (
    ("table", r"таблиц|табличн"),
    ("histogram", r"гистограмм"),
    ("box", r"ящик|boxplot|box plot"),
    ("heatmap", r"теплов\w* карт|хитмап|heatmap"),
    ("scatter", r"точечн|рассеяни|scatter"),
    ("area", r"с заливкой|площадн|area chart"),
    ("line", r"линейн|линией|line chart"),
    ("bar", r"столбч|столбик|bar chart"),
)
_GENERIC = re.compile(
    r"в другом виде|по-другому|по другому|покажи иначе|"
    r"друг\w* (вид|график|диаграмм|визуализац)|"
    r"(смени|поменяй|измени)\w* (вид|тип) (графика|диаграммы|визуализации)"
)
_REDRAW = re.compile(
    r"сделай|переделай|перестрой|перерисуй|преврати|замени|смени|поменяй|переключи|"
    r"то же самое|тоже самое|то же|это же|этот же|эти же|в виде|вместо|"
    r"покажи (это|его|их|данные|результат)|а теперь|а сейчас"
)


def detect_morph(question: str) -> str | None:
    """Распознать просьбу сменить вид прошлого графика.

    Возвращает целевой тип графика, AUTO для «покажи в другом виде»
    или None, если вопрос — новое исследование.
    """
    text = question.lower().replace("ё", "е")
    target = next((kind for kind, words in _TYPE_WORDS if re.search(words, text)), None)
    if target and _REDRAW.search(text):
        return target
    if _GENERIC.search(text):
        return AUTO
    return None


def morph_candidates(current_type: str, to_type: str) -> list[str]:
    """Виды, в которые можно перестроить график, в порядке приоритета."""
    if to_type in CHART_TYPES:
        return [to_type]
    allowed = MORPH_MAP.get(current_type, [])
    return allowed or [current_type]


def morph(
    current_type: str,
    to_type: str,
    data: list[dict],
    titles: dict[str, str] | None = None,
    result_table: str | None = None,
) -> dict:
    """Перестроить спеку в другой вид из тех же данных (без SQL и LLM).

    Явно названный вид выполняется как есть; для AUTO берётся первый
    вид из MORPH_MAP, который на этих данных отличается от текущего.
    """
    spec: dict = {}
    for candidate in morph_candidates(current_type, to_type):
        spec = build_vega_spec(candidate, result_table, data=data, titles=titles)
        if spec["usermeta"]["chart_type"] != current_type:
            break
    return spec
