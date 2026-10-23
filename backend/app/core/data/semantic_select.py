"""Семантический отбор колонок под запрос.

Т.к. размер таблицы не контролируем, отдавать всю схему LLM небезопасно
(раздувание контекста). Берём топ-K релевантных колонок — модель дальше
сама решает, что использовать (см. prompts/step_planner.py).

Для отобранных колонок describe_columns собирает выжимку паспорта: название,
тип, описание, диапазон, частые значения. Контур замкнутый, поэтому значения из данных
модели отдавать можно — так она видит, как реально записана категория
(«Электроника» / «електроника»), без отдельного шага разведки.
"""

import re

from app.config import settings
from app.core.data.profiling import ColumnStats, TableProfile, flag_notes

TOKEN_WEIGHT = 2
KIND_WEIGHT = 1
# Слова с общим началом такой длины считаем одним словом в разных формах
# («выручка» / «выручку», «электроника» / «электроники»).
STEM_LENGTH = 5
# Сколько частых значений категории показать модели и до какой длины их резать.
PROMPT_TOP_VALUES = 10
PROMPT_VALUE_CHARS = 40
# Подсказка к флагу качества: как считать такую колонку.
FLAG_HINTS = {"numeric_as_text": "считай через TRY_CAST(... AS DOUBLE)"}
AGGREGATION_HINTS = (
    "сумм",
    "средн",
    "итог",
    "сколько",
    "количеств",
    "максим",
    "миним",
    "медиан",
    "доля",
    "sum",
    "total",
    "avg",
    "average",
    "mean",
    "count",
    "max",
    "min",
)
TREND_HINTS = (
    "динамик",
    "тренд",
    "месяц",
    "недел",
    "квартал",
    "сезон",
    "ежедн",
    "по дням",
    "по годам",
    "период",
    "trend",
    "month",
    "week",
    "daily",
    "quarter",
    "year",
)


def tokenize(text: str) -> set[str]:
    return {token for token in re.split(r"[^\w]+", text.lower()) if token}


def _same_word(left: str, right: str) -> bool:
    if left == right:
        return True
    return min(len(left), len(right)) >= STEM_LENGTH and left[:STEM_LENGTH] == right[:STEM_LENGTH]


def _matches(question_tokens: set[str], column_tokens: set[str]) -> int:
    """Сколько слов вопроса встречается среди слов колонки (с учётом словоформ)."""
    return sum(any(_same_word(q, c) for c in column_tokens) for q in question_tokens)


def preferred_kinds(question: str) -> set[str]:
    lowered = question.lower()
    kinds = set()
    if any(hint in lowered for hint in AGGREGATION_HINTS):
        kinds.add("numeric")
    if any(hint in lowered for hint in TREND_HINTS):
        kinds.add("temporal")
    return kinds


def select_columns(
    question: str,
    columns: list[str],
    top_k: int | None = None,
    profile: TableProfile | None = None,
    titles: dict[str, str] | None = None,
) -> list[str]:
    """Топ-K колонок: совпадение слов вопроса с именем, названием и частыми
    значениями категорий + бонус за тип.

    titles — человекочитаемые названия колонок из описания таблицы в паспорте:
    по ним русский вопрос находит колонки с латинскими именами («выручка» -> revenue).
    """
    limit = top_k or settings.semantic_top_k
    question_tokens = tokenize(question)
    wanted = preferred_kinds(question) if profile else set()
    kinds = {column.name: column.kind for column in profile.columns} if profile else {}
    # Вопрос называет значение категории («электроники») — колонка с ним нужна.
    values = {
        column.name: " ".join(str(entry["value"]) for entry in column.top_values)
        for column in (profile.columns if profile else [])
        if column.kind == "text"
    }

    def score(column: str) -> int:
        words = " ".join(
            [column.replace("_", " "), (titles or {}).get(column, ""), values.get(column, "")]
        )
        matches = _matches(question_tokens, tokenize(words))
        bonus = KIND_WEIGHT if kinds.get(column) in wanted else 0
        return TOKEN_WEIGHT * matches + bonus

    return sorted(columns, key=lambda column: -score(column))[:limit]


def describe_columns(
    columns: list[str],
    profile: TableProfile | None = None,
    descriptions: dict[str, str] | None = None,
    titles: dict[str, str] | None = None,
) -> str:
    """Выжимка паспорта по колонкам для промпта: по строке на колонку."""
    stats = {column.name: column for column in profile.columns} if profile else {}
    lines = []
    for name in columns:
        column = stats.get(name)
        line = f"- {name}"
        if (titles or {}).get(name):
            line += f" «{titles[name]}»"
        if column is not None:
            line += f" [{column.kind}, {column.dtype}]"
        if (descriptions or {}).get(name):
            line += f" — {descriptions[name]}"
        facts = _column_facts(column) if column is not None else ""
        if facts:
            line += f"; {facts}"
        marks = _flag_marks(column) if column is not None else ""
        if marks:
            line += f"; {marks}"
        lines.append(line)
    return "\n".join(lines) or "неизвестно"


def _flag_marks(column: ColumnStats) -> str:
    """Флаги качества короткими метками: «⚠ числа записаны текстом, считай через TRY_CAST»."""
    marks = []
    for flag, note in zip(column.flags, flag_notes(column), strict=True):
        hint = FLAG_HINTS.get(flag)
        marks.append(f"⚠ {note}, {hint}" if hint else f"⚠ {note}")
    return "; ".join(marks)


def _column_facts(column: ColumnStats) -> str:
    if column.kind == "numeric":
        if column.min is None:
            return ""
        median = f", медиана {_short(column.median)}" if column.median is not None else ""
        return f"от {_short(column.min)} до {_short(column.max)}{median}"
    if column.kind == "temporal":
        if column.min_date is None:
            return ""
        return f"с {_short(column.min_date)} по {_short(column.max_date)}"
    # Уникальные строки с множеством значений — ключи или свободный текст, не категории.
    free_text = column.is_unique and (column.distinct or 0) > PROMPT_TOP_VALUES
    if column.kind == "text" and column.top_values and not free_text:
        values = ", ".join(
            f'"{_short(entry["value"])}"' for entry in column.top_values[:PROMPT_TOP_VALUES]
        )
        distinct = f"{column.distinct} разных, " if column.distinct is not None else ""
        return f"{distinct}частые значения: {values}"
    return ""


def _short(value: object) -> str:
    text = str(value)
    if isinstance(value, float):
        # Без экспоненты: 125000 и 28.5, а не 1.25e+05; мелкие доли — 4 значащие цифры.
        text = f"{value:.2f}".rstrip("0").rstrip(".") if abs(value) >= 1 else f"{value:.4g}"
    return text if len(text) <= PROMPT_VALUE_CHARS else text[: PROMPT_VALUE_CHARS - 1] + "…"
