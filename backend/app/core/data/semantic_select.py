"""Семантический отбор колонок под запрос.

Т.к. размер таблицы не контролируем, отдавать всю схему LLM небезопасно
(раздувание контекста). Берём топ-K релевантных колонок — модель дальше
сама решает, что использовать (см. prompts/step_planner.py).
"""
from app.config import settings


def select_columns(question: str, columns: list[str], top_k: int | None = None) -> list[str]:
    """Отобрать топ-K колонок, релевантных вопросу.

    TODO(round-2): эмбеддинги (sentence-transformers, локально) вместо
    наивного лексического пересечения. Сейчас — пересечение токенов.
    """
    k = top_k or settings.semantic_top_k
    q_tokens = set(question.lower().replace("?", "").split())
    scored = sorted(
        columns,
        key=lambda c: -len(q_tokens & set(c.lower().split("_"))),
    )
    return scored[:k]
