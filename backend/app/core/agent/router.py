"""Быстрый роутер простых вопросов: шаблонный план без планировщика.

Роутер перехватывает только однозначные формы («сколько строк», «общая
сумма amount», «топ-5 категорий по выручке», «выручка по месяцам»,
«распределение amount»). Фильтры, несколько условий и неоднозначные
колонки уходят в LLM: ошибка роутера дороже одного вызова планировщика.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.core.data.profiling import TableProfile
from app.models.research_step import ResearchPlan, ResearchStep

RouteKind = Literal[
    "simple_count",
    "simple_total",
    "simple_avg",
    "simple_top",
    "simple_time",
    "simple_distribution",
]

STEM_LENGTH = 4

_INTERVALS = {
    "дн": "day",
    "день": "day",
    "дням": "day",
    "недел": "week",
    "месяц": "month",
    "квартал": "quarter",
    "год": "year",
    "годам": "year",
}
_PERIOD_WORDS = re.compile(
    r"(?:по|за)\s+(дням|день|дн|недел\w*|месяц\w*|квартал\w*|год\w*)", re.IGNORECASE
)
_TREND_WORDS = re.compile(r"динамик|тренд|во\s+времени", re.IGNORECASE)
_COMPLEX = re.compile(
    r"\b(где|если|кроме|сравн\w*|корреляц\w*|зависим\w*|фильтр\w*|"
    r"доля|долю|процент\w*|больше|меньше|выше|ниже|равно)\b"
    r"|\b(?:за|в|с|по)\s+(?:19|20)\d{2}\b"
    r"|\bза\s+(?:последн\w*|прошл\w*|этот|текущ\w*|год|квартал|месяц|недел\w*)",
    re.IGNORECASE,
)
_COUNT = re.compile(
    r"^\s*(?:сколько|какое\s+количество|число)\b.*\b(?:строк|записей|наблюдени\w*|рядов)\b",
    re.IGNORECASE,
)
_TOTAL = re.compile(r"\b(общ\w*|суммарн\w*|итог\w*|сумм[аыу]?)\b", re.IGNORECASE)
_AVG = re.compile(r"\b(средн\w*)\b", re.IGNORECASE)
_TOP = re.compile(r"\bтоп[-\s]?(\d+)?\b|\b(первые|лучшие|худшие)\b", re.IGNORECASE)
_DISTRIBUTION = re.compile(r"\bраспределени\w*\b", re.IGNORECASE)
# «по регионам», «по категориям» — это разрез, простой шаблон его не покрывает.
_GROUP_BY = re.compile(r"\bпо\s+[a-zа-я0-9_]+", re.IGNORECASE)

# Русские слова вопроса -> английские/русские корни имён колонок.
_SYNONYMS = {
    "выручк": ("revenue", "sales", "выручк", "доход"),
    "доход": ("revenue", "income", "доход"),
    "сумм": ("amount", "sum", "total", "сумм", "стоим"),
    "стоимост": ("price", "cost", "amount", "стоим", "цен"),
    "цен": ("price", "cost", "цен"),
    "количеств": ("quantity", "qty", "count", "количеств"),
    "прибыл": ("profit", "маржа", "прибыл"),
    "заказ": ("order", "orders", "заказ"),
    "пользовател": ("user", "customer", "client", "пользовател", "клиент"),
    "клиент": ("customer", "client", "user", "клиент"),
    "товар": ("product", "item", "товар"),
    "категор": ("category", "категор"),
    "регион": ("region", "город", "регион"),
    "город": ("city", "region", "город"),
    "дат": ("date", "datetime", "дата", "врем"),
    "врем": ("date", "time", "created", "дата", "врем"),
    "период": ("date", "period", "month", "период"),
}


@dataclass(frozen=True)
class Route:
    """Результат роутера: вид шаблона, готовый план и причина выбора."""

    kind: RouteKind
    plan: ResearchPlan
    reason: str


def simple_plan(
    question: str,
    profile: TableProfile | None,
    titles: dict[str, str] | None = None,
) -> Route | None:
    """Вернуть шаблонный план, если вопрос простой и колонки однозначны."""
    if profile is None or not question.strip():
        return None
    text = _normalize(question)
    if _COMPLEX.search(text) or "дашборд" in text or "панел" in text:
        return None
    interval = _time_interval(text)
    wants_trend = _TREND_WORDS.search(text) is not None
    if interval is not None or wants_trend:
        return _time_route(question, profile, titles, interval or "month")
    if _DISTRIBUTION.search(text):
        if _GROUP_BY.search(text):
            return None
        metric = resolve_metric(question, profile, titles)
        if metric is None:
            return None
        return Route(
            "simple_distribution",
            ResearchPlan(
                steps=[
                    ResearchStep(
                        step_id=0,
                        tool="select_columns",
                        input="$source",
                        arguments={"columns": [metric]},
                        purpose=f"Значения {metric}",
                    )
                ],
                final_viz="histogram",
            ),
            f"распределение по {metric}",
        )
    top = _TOP.search(text)
    if top is not None:
        return _top_route(question, profile, titles, top)
    if _COUNT.search(text):
        if _GROUP_BY.search(text):
            return None
        return Route(
            "simple_count",
            ResearchPlan(
                steps=[
                    ResearchStep(
                        step_id=0,
                        tool="group_by",
                        input="$source",
                        arguments={"by": [], "function": "COUNT"},
                        purpose="Число строк",
                    )
                ],
                final_viz="kpi",
            ),
            "число строк",
        )
    if _TOTAL.search(text) and not _AVG.search(text):
        if _GROUP_BY.search(text):
            return None
        metric = resolve_metric(question, profile, titles)
        if metric is None:
            return None
        return Route(
            "simple_total",
            ResearchPlan(
                steps=[
                    ResearchStep(
                        step_id=0,
                        tool="group_by",
                        input="$source",
                        arguments={"by": [], "function": "SUM", "column": metric},
                        purpose=f"Сумма {metric}",
                    )
                ],
                final_viz="kpi",
            ),
            f"сумма {metric}",
        )
    if _AVG.search(text):
        if _GROUP_BY.search(text):
            return None
        metric = resolve_metric(question, profile, titles)
        if metric is None:
            return None
        return Route(
            "simple_avg",
            ResearchPlan(
                steps=[
                    ResearchStep(
                        step_id=0,
                        tool="group_by",
                        input="$source",
                        arguments={"by": [], "function": "AVG", "column": metric},
                        purpose=f"Среднее {metric}",
                    )
                ],
                final_viz="kpi",
            ),
            f"среднее {metric}",
        )
    return None


def resolve_metric(
    question: str, profile: TableProfile | None, titles: dict[str, str] | None = None
) -> str | None:
    """Однозначная числовая колонка вопроса (None — пусть решает LLM)."""
    if profile is None:
        return None
    candidates = [column for column in profile.columns if column.kind == "numeric"]
    return _resolve(question, candidates, titles)


def resolve_category(
    question: str, profile: TableProfile | None, titles: dict[str, str] | None = None
) -> str | None:
    """Однозначная категориальная колонка вопроса."""
    if profile is None:
        return None
    candidates = [column for column in profile.columns if column.kind in ("text", "identifier")]
    return _resolve(question, candidates, titles)


def resolve_date(question: str, profile: TableProfile | None) -> str | None:
    """Однозначная временная колонка вопроса."""
    if profile is None:
        return None
    candidates = [column for column in profile.columns if column.kind == "temporal"]
    return _resolve(question, candidates, None)


def _resolve(question: str, candidates: list, titles: dict[str, str] | None) -> str | None:
    if not candidates:
        return None
    text = _normalize(question)
    tokens = _tokens(text)
    scored: list[tuple[int, str]] = []
    for column in candidates:
        score = _score(text, tokens, column.name, (titles or {}).get(column.name, ""))
        if score:
            scored.append((score, column.name))
    if not scored:
        return candidates[0].name if len(candidates) == 1 else None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def _score(text: str, tokens: set[str], name: str, title: str) -> int:
    score = 0
    variants = {_normalize(name), _normalize(name).replace("_", " "), _normalize(title or "")}
    for variant in variants:
        if variant and len(variant) >= 3 and variant in text:
            score += 4
    column_tokens = _tokens(f"{name} {title}")
    for token in tokens:
        if any(_same_word(token, column) for column in column_tokens):
            score += 3
        for stem, synonyms in _SYNONYMS.items():
            if token.startswith(stem) and any(
                _same_word(syn, column) for syn in synonyms for column in column_tokens
            ):
                score += 2
                break
    return score


def _same_word(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return min(len(left), len(right)) >= STEM_LENGTH and left[:STEM_LENGTH] == right[:STEM_LENGTH]


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^\w]+", text.lower()) if token}


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def _time_interval(text: str) -> str | None:
    match = _PERIOD_WORDS.search(text)
    if match is None:
        return None
    word = match.group(1).lower()
    for stem, interval in _INTERVALS.items():
        if word.startswith(stem):
            return interval
    return None


def _time_route(
    question: str,
    profile: TableProfile,
    titles: dict[str, str] | None,
    interval: str,
) -> Route | None:
    date_column = resolve_date(question, profile)
    if date_column is None:
        return None
    text = _normalize(question)
    metric = (
        None
        if re.search(r"строк|записей|количеств", text)
        else resolve_metric(question, profile, titles)
    )
    function = "AVG" if _AVG.search(text) else "SUM"
    arguments: dict[str, object] = {
        "date_column": date_column,
        "interval": interval,
        "function": "COUNT" if metric is None else function,
    }
    if metric is not None:
        arguments["metric_column"] = metric
    return Route(
        "simple_time",
        ResearchPlan(
            steps=[
                ResearchStep(
                    step_id=0,
                    tool="time_bucket",
                    input="$source",
                    arguments=arguments,
                    purpose=f"По периодам ({interval})",
                )
            ],
            final_viz="line",
        ),
        f"динамика по {date_column}",
    )


def _top_route(
    question: str, profile: TableProfile, titles: dict[str, str] | None, match: re.Match[str]
) -> Route | None:
    category = resolve_category(question, profile, titles)
    metric = resolve_metric(question, profile, titles)
    if category is None or metric is None or category == metric:
        return None
    limit = int(match.group(1)) if match.group(1) else 5
    alias = f"sum_{metric}"
    return Route(
        "simple_top",
        ResearchPlan(
            steps=[
                ResearchStep(
                    step_id=0,
                    tool="group_by",
                    input="$source",
                    arguments={"by": [category], "function": "SUM", "column": metric},
                    purpose=f"Сумма {metric} по {category}",
                ),
                ResearchStep(
                    step_id=1,
                    tool="sort_limit",
                    input="$prev",
                    arguments={"order_by": alias, "descending": True, "limit": limit},
                    purpose=f"Топ-{limit} по {metric}",
                ),
            ],
            final_viz="bar",
        ),
        f"топ-{limit} {category} по {metric}",
    )
