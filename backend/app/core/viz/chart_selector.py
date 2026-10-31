"""Выбор типа визуализации под вопрос без LLM и проверка вида по данным.

Вид и колонки графика выбирает агент по готовым результатам (см.
viz/chart_choice.py, agent/chart_pick.py); select_chart — запасной путь,
когда выбора нет: явный вид от планировщика или ключевые слова вопроса,
проверенные по строкам результата.
"""

import math
import re

from app.core.viz.vega_spec_builder import infer_field_types, ordered_column

NO_CHART = "none"
# Больше 12 категорий столбцы лежат (vega_spec_builder), поэтому до 50 читаются.
MAX_BAR_CATEGORIES = 50
MIN_LINE_POINTS = 3
MIN_HISTOGRAM_VALUES = 20
# Явная просьба о графике важнее выбора планировщика «table».
_CHART_WORDS = re.compile(
    r"\b(?:график\w*|диаграмм\w*|chart|plot|визуализ\w*|нарисуй\w*|постро\w*)",
    re.IGNORECASE,
)
_TABLE_WORDS = re.compile(r"\bтаблиц\w*", re.IGNORECASE)
# Слова временной оси: «от даты», «по месяцам», «динамика за год».
_TIME_WORDS = re.compile(
    r"\b(?:дат\w*|врем\w*|период\w*|год\w*|месяц\w*|квартал\w*|недел\w*|дн\w*)",
    re.IGNORECASE,
)


def asks_chart(question: str) -> bool:
    """Пользователь просит график, а не таблицу.

    «Покажи таблицей» и «таблица с графиком» — просьба о таблице; «график,
    а не таблицу» — наоборот, просьба о графике.
    """
    if _CHART_WORDS.search(question) is None:
        return False
    for match in _TABLE_WORDS.finditer(question):
        prefix = question[max(0, match.start() - 4) : match.start()].lower()
        if not re.search(r"(?:не|без)\s*$", prefix):
            return False
    return True


def is_single_number(records: list[dict] | None) -> bool:
    if records is None or len(records) != 1:
        return False
    if list(infer_field_types(records).values()) != ["quantitative"]:
        return False
    return all(math.isfinite(value) for value in records[0].values())


def fits(
    chart_type: str, records: list[dict], types: dict[str, str], column: str | None = None
) -> bool:
    """Подходит ли вид к строкам результата; column — колонка гистограммы."""
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
        column = column or (numeric[0] if len(numeric) == 1 else None)
        return column is not None and _present(records, column) >= MIN_HISTOGRAM_VALUES
    if chart_type == "box":
        return bool(numeric)
    if chart_type == "bar":
        return bool(categories) and _distinct(records, categories[0]) <= MAX_BAR_CATEGORIES
    if chart_type == "heatmap":
        return len(categories) >= 2
    if chart_type == "kpi":
        return is_single_number(records)
    return chart_type == "table"


def fits_columns(
    chart_type: str, records: list[dict], types: dict[str, str], x: str | None, y: str | None
) -> bool:
    """Подходит ли вид к строкам по названным колонкам x и y (выбор агента).

    В отличие от fits, ось x может быть любой колонкой: целочисленный ключ
    (store_id) — категория столбцов, год числом — ось линии.
    """

    def numeric(column: str | None) -> bool:
        return column is not None and types.get(column) == "quantitative"

    if y is not None and chart_type not in ("heatmap", "scatter") and not numeric(y):
        return False
    if chart_type in ("line", "area"):
        return x is not None and _distinct(records, x) >= MIN_LINE_POINTS
    if chart_type == "scatter":
        return numeric(x) and numeric(y) and x != y
    if chart_type == "histogram":
        column = y or x
        return numeric(column) and _present(records, column) >= MIN_HISTOGRAM_VALUES
    if chart_type == "box":
        return numeric(y)
    if chart_type == "bar":
        return x is not None and _distinct(records, x) <= MAX_BAR_CATEGORIES
    if chart_type == "heatmap":
        return x is not None and y is not None and x != y
    if chart_type == "kpi":
        return is_single_number(records)
    return chart_type == "table"


def select_chart(
    question: str,
    result_table: str,
    requested: str = "auto",
    records: list[dict] | None = None,
    titles: dict[str, str] | None = None,
) -> str:
    """Подобрать тип графика: bar | line | scatter | histogram | kpi | table (или none).

    Результат из одной строки — kpi (одно конечное число) или table. Явный
    requested или эвристика по ключевым словам вопроса проверяются по
    records через fits; не подошло — table. Без records проверки нет.
    Гистограмма по таблице с несколькими числовыми колонками строится по
    колонке, названной в вопросе (histogram_column).
    """
    if requested == NO_CHART:
        return NO_CHART
    if records is not None and len(records) <= 1:
        return "kpi" if is_single_number(records) else "table"
    chart_type = requested if requested != "auto" else _by_question(question)
    if records is None:
        return chart_type
    types = infer_field_types(records)
    column = histogram_column(question, types, titles) if chart_type == "histogram" else None
    if chart_type == "table":
        # Планировщик мог ошибиться: явная просьба о графике по временному
        # ряду важнее его «table». «Покажи таблицей» оставляем таблицей.
        if asks_chart(question) and fits("line", records, types):
            return "line"
        return "table"
    if fits(chart_type, records, types, column):
        return chart_type
    # Запрошенный вид не подошёл: для временного ряда линия информативнее
    # таблицы (bar с сотнями дат не проходит по числу категорий).
    return "line" if fits("line", records, types) else "table"


def histogram_column(
    question: str, types: dict[str, str], titles: dict[str, str] | None = None
) -> str | None:
    """Числовая колонка для гистограммы: единственная или названная в вопросе.

    Колонку узнаём по имени или по началу слов её названия («цен» из «Цена»),
    чтобы «распределение цен» нашло колонку price с названием «Цена».
    """
    numeric = [column for column, kind in types.items() if kind == "quantitative"]
    if len(numeric) == 1:
        return numeric[0]
    text = question.lower()
    return next(
        (column for column in numeric if _named(text, column, (titles or {}).get(column, ""))),
        None,
    )


def asks_distribution(question: str) -> bool:
    text = question.lower()
    return "распредел" in text or "гистограмм" in text


def _by_question(question: str) -> str:
    text = question.lower()
    if asks_distribution(question):
        return "histogram"
    if _TIME_WORDS.search(text) and ("график" in text or "зависим" in text or "от дат" in text):
        return "line"
    if "зависим" in text:
        return "scatter"
    if (
        "динамик" in text
        or "во времени" in text
        or "по годам" in text
        or "по месяцам" in text
        or "по датам" in text
        or "по дням" in text
    ):
        return "line"
    if "сравн" in text or "по категори" in text:
        return "bar"
    if "связ" in text or "корреляц" in text:
        return "scatter"
    return "bar"


def _named(text: str, column: str, title: str) -> bool:
    if len(column) >= 3 and column.lower() in text:
        return True
    words = re.findall(r"\w{3,}", title.lower())
    return any(word[: max(3, len(word) - 2)] in text for word in words)


def _present(records: list[dict], column: str) -> int:
    return sum(record.get(column) is not None for record in records)


def _distinct(records: list[dict], column: str) -> int:
    return len({repr(record.get(column)) for record in records})
