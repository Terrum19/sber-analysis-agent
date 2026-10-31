"""Что и как посчитано в плитке дашборда (#224): текст ответа из выполненных шагов.

Без LLM: шаги плитки уже выполнены, их аргументы описывают расчёт точно.
Колонки называются подписями из паспорта («Дата операции»), а не именами.
"""

import math
import re
from decimal import Decimal

from app.core.tools.registry import tool_registry
from app.models.research_step import ResearchStep

AGGREGATES = {
    "COUNT": "количество",
    "COUNT_DISTINCT": "число уникальных",
    "SUM": "сумма",
    "AVG": "среднее",
    "MIN": "минимум",
    "MAX": "максимум",
    "MEDIAN": "медиана",
    "STDDEV": "стандартное отклонение",
}
# Колонка периода из быстрых плиток (rich_tiles): date_trunc('month', "Дата").
PERIOD_COLUMN = "period"
TRUNCATED = re.compile(r"date_trunc\('(\w+)', (.+)\)")
PERIODS = {
    "day": "по дням",
    "week": "по неделям",
    "month": "по месяцам",
    "quarter": "по кварталам",
    "year": "по годам",
}


def explain_tile(steps: list[ResearchStep], titles: dict[str, str], records: list[dict]) -> str:
    """«как посчитано» плитки одной фразой; у KPI — и итоговое число."""
    parts = [text for step in steps if (text := _explain_step(step, titles))]
    text = "; ".join(parts) or "расчёт по исходной таблице"
    value = _single_value(records)
    return f"{text} = {value}" if value is not None else text


def _explain_step(step: ResearchStep, titles: dict[str, str]) -> str:
    try:
        args = tool_registry.get(step.tool).parse_args(step.columns, step.arguments)
    except (KeyError, ValueError):
        # Тула нет в реестре или аргументы не разобрались — цель шага из плана.
        return step.purpose or step.tool or ""

    def name(column: str | None) -> str:
        if column == PERIOD_COLUMN and column not in titles:
            return "«период»"
        return f"«{titles.get(column, column)}»" if column else ""

    def metric(function: str, column: str | None) -> str:
        if column is None:
            return "количество строк"
        return f"{AGGREGATES.get(function, function.lower())} {name(column)}"

    if step.tool == "group_by":
        by = [args.by] if isinstance(args.by, str) else list(args.by)
        metrics = args.metrics or None
        measured = (
            ", ".join(metric(item.function, item.column) for item in metrics)
            if metrics
            else metric(args.function, args.column)
        )
        return f"{measured} по {', '.join(name(column) for column in by)}" if by else measured
    if step.tool == "time_bucket":
        by = "".join(f" и {name(column)}" for column in args.by)
        period = PERIODS.get(args.interval, args.interval)
        return (
            f"{metric(args.function, args.metric_column)} {period} ({name(args.date_column)}){by}"
        )
    if step.tool == "filter_rows":
        value = args.value if args.value is not None else "пусто"
        return f"только строки, где {name(args.column)} {args.operator} {value}"
    if step.tool == "create_column":
        if args.new_column == PERIOD_COLUMN and (found := TRUNCATED.fullmatch(args.expression)):
            period = PERIODS.get(found[1], found[1])
            return f"разбивка {period} ({name(found[2].strip(chr(34)))})"
        return f"новый показатель {name(args.new_column)} = {args.expression}"
    if step.tool == "sort_limit":
        order = args.order_by if isinstance(args.order_by, str) else ", ".join(args.order_by)
        top = f"топ-{args.limit}" if args.limit else "сортировка"
        return f"{top} по {name(order)}"
    return step.purpose or step.tool or ""


def _single_value(records: list[dict]) -> str | None:
    """Итог KPI: одна строка с одним числом."""
    if len(records) != 1 or len(records[0]) != 1:
        return None
    value = next(iter(records[0].values()))
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    text = f"{value:,.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else f"{value:,}"
    return text.replace(",", " ")
