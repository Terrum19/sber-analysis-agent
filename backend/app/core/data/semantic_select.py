"""Семантический отбор колонок под запрос.

Т.к. размер таблицы не контролируем, отдавать всю схему LLM небезопасно
(раздувание контекста). Берём топ-K релевантных колонок — модель дальше
сама решает, что использовать (см. prompts/step_planner.py).
"""

import re

from app.config import settings
from app.core.data.profiling import TableProfile

TOKEN_WEIGHT = 2
KIND_WEIGHT = 1
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
) -> list[str]:
    limit = top_k or settings.semantic_top_k
    question_tokens = tokenize(question)
    wanted = preferred_kinds(question) if profile else set()
    kinds = {column.name: column.kind for column in profile.columns} if profile else {}

    def score(column: str) -> int:
        matches = len(question_tokens & tokenize(column.replace("_", " ")))
        bonus = KIND_WEIGHT if kinds.get(column) in wanted else 0
        return TOKEN_WEIGHT * matches + bonus

    return sorted(columns, key=lambda column: -score(column))[:limit]
