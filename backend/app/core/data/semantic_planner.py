"""Вопрос -> semantic intent: метрики, измерения, фильтры, время (P1)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models.semantic import SemanticDimension, SemanticJoin, SemanticMetric, SemanticModel

TIME_GRAINS = {
    "дн": "day",
    "недел": "week",
    "месяц": "month",
    "квартал": "quarter",
    "год": "year",
}
_AGGREGATION_WORDS = {
    "сумм": "SUM",
    "общ": "SUM",
    "итог": "SUM",
    "средн": "AVG",
    "количеств": "COUNT",
    "сколько": "COUNT",
    "максим": "MAX",
    "миним": "MIN",
    "медиан": "MEDIAN",
}


@dataclass(frozen=True)
class SemanticIntent:
    metrics: list[SemanticMetric] = field(default_factory=list)
    dimensions: list[SemanticDimension] = field(default_factory=list)
    filters: list[dict[str, Any]] = field(default_factory=list)
    time_range: tuple[str, str] | None = None
    time_grain: str | None = None
    joins: list[SemanticJoin] = field(default_factory=list)


class SemanticPlanner:
    """Лексический маппинг вопроса на управляемую семантику.

    Эмбеддинги — следующий шаг; здесь основа: имена, titles и синонимы
    метрик/измерений, агрегатные слова и временные зёрна. Неоднозначность
    не угадывается: возвращается пустой/частичный intent, и вызывающий
    код задаёт уточняющий вопрос.
    """

    def map(self, question: str, model: SemanticModel) -> SemanticIntent:
        text = _normalize(question)
        metrics = [metric for metric in model.metrics if _matches(text, metric)]
        dimensions = [dimension for dimension in model.dimensions if _matches(text, dimension)]
        grain = _time_grain(text)
        temporal = [dimension for dimension in dimensions if dimension.kind == "temporal"]
        if grain and not temporal and model.default_time_dimension:
            default = next(
                (
                    dimension
                    for dimension in model.dimensions
                    if dimension.name == model.default_time_dimension
                ),
                None,
            )
            if default is not None:
                temporal = [default]
        if temporal:
            dimensions = [dimension for dimension in dimensions if dimension.kind != "temporal"]
            dimensions.extend(temporal)
        return SemanticIntent(
            metrics=metrics,
            dimensions=dimensions,
            time_range=_time_range(text),
            time_grain=grain if temporal else None,
        )

    def to_prompt(self, model: SemanticModel, intent: SemanticIntent) -> str:
        """Компактное описание семантики для planner-промпта."""
        lines = [f"Таблица {model.table}: {model.description}".strip()]
        if model.metrics:
            lines.append(
                "Метрики: "
                + ", ".join(
                    f"{metric.name} «{metric.title}» ({metric.aggregation}"
                    f"{f' {metric.column}' if metric.column else ''})"
                    for metric in model.metrics
                )
            )
        if model.dimensions:
            lines.append(
                "Измерения: "
                + ", ".join(
                    f"{dimension.name} «{dimension.column}» ({dimension.kind})"
                    for dimension in model.dimensions
                )
            )
        if intent.metrics:
            lines.append("В вопросе найдены метрики: " + ", ".join(m.name for m in intent.metrics))
        if intent.time_grain:
            lines.append(f"Временное зерно: {intent.time_grain}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Ambiguity:
    """Неоднозначность вопроса: какие варианты и почему нужно уточнить."""

    question: str
    candidates: list[str]
    reason: str


class ClarificationPolicy:
    """Решает, когда semantic planner должен задать один уточняющий вопрос.

    Политика намеренно консервативна: несколько метрик в вопросе — это
    нормальный составной запрос, а неоднозначность фиксируется, когда
    метрики не найдены, но вопрос выглядит как запрос метрики.
    """

    def should_ask(self, intent: SemanticIntent, question: str = "") -> Ambiguity | None:
        if intent.metrics:
            return None
        text = _normalize(question)
        if any(stem in text for stem in _AGGREGATION_WORDS):
            return Ambiguity(
                question=question,
                candidates=[],
                reason="не удалось однозначно сопоставить метрику",
            )
        return None


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def _matches(text: str, item: SemanticMetric | SemanticDimension) -> bool:
    names = [item.name, getattr(item, "column", ""), getattr(item, "title", "")]
    names += list(item.synonyms)
    for name in names:
        if not name:
            continue
        normalized = name.lower().replace("_", " ")
        if normalized in text:
            return True
        tokens = [token for token in re.split(r"[^\w]+", normalized) if len(token) >= 4]
        if tokens and all(token[:4] in text for token in tokens):
            return True
    return False


def _time_grain(text: str) -> str | None:
    if not re.search(r"по\s+|динамик|тренд|во\s+времени|за\s+(?:каждый|последн)", text):
        return None
    for stem, grain in TIME_GRAINS.items():
        if stem in text:
            return grain
    return None


def _time_range(text: str) -> tuple[str, str] | None:
    """Простые явные периоды: «с 2024-01-01 по 2024-12-31», «за 2024»."""
    match = re.search(r"\bс\s+(\d{4}-\d{2}-\d{2})\s+по\s+(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1), match.group(2)
    match = re.search(r"\b(?:за|в)\s+(20\d{2})\b", text)
    if match:
        year = match.group(1)
        return f"{year}-01-01", f"{year}-12-31"
    return None


def _aggregation_hint(text: str) -> str | None:
    for stem, aggregation in _AGGREGATION_WORDS.items():
        if stem in text:
            return aggregation
    return None
