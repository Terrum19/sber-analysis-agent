"""Быстрые плитки дашборда (#224, вариант quick_tiles): шаги из полей плана, без LLM.

Простая плитка — «агрегат показателя в разрезе по периодам»: план дашборда
отдаёт её поля (metric, agg, by, period, date_column, top, фильтр), а шаги
group_by / time_bucket / sort_limit / filter_rows собирает этот модуль.
Поля не сходятся с таблицей — None: плитку строит обычный планировщик.
"""

from app.core.agent.planner import validate_plan
from app.core.tools.sql.atomic import aggregate_alias
from app.models.dashboard_plan import OutlineTile
from app.models.research_step import PREVIOUS, SOURCE, ResearchPlan, ResearchStep

# Агрегаты, которым нужна числовая колонка.
NUMERIC_AGGS = {"SUM", "AVG", "MIN", "MAX", "MEDIAN", "STDDEV"}


def quick_plan(tile: OutlineTile, kinds: dict[str, str], start: int) -> ResearchPlan | None:
    """План простой плитки по её полям; kinds — колонка -> вид из паспорта таблицы.

    None — плитка сложная или поля не подходят к колонкам таблицы.
    """
    if tile.kind != "simple" or tile.agg is None or not _fits_table(tile, kinds):
        return None
    steps: list[ResearchStep] = []

    def add(tool: str, arguments: dict) -> None:
        steps.append(
            ResearchStep(
                step_id=start + len(steps),
                tool=tool,
                input=PREVIOUS if steps else SOURCE,
                arguments=arguments,
                purpose=tile.title,
            )
        )

    if tile.filter_column is not None:
        add("filter_rows", {"column": tile.filter_column, "value": tile.filter_value})
    if tile.period is not None:
        add(
            "time_bucket",
            {
                "date_column": tile.date_column,
                "interval": tile.period,
                "metric_column": tile.metric,
                "function": tile.agg,
                "by": [tile.by] if tile.by else [],
            },
        )
    else:
        add(
            "group_by",
            {"by": [tile.by] if tile.by else [], "column": tile.metric, "function": tile.agg},
        )
        if tile.by and tile.top:
            add(
                "sort_limit",
                {
                    "order_by": aggregate_alias(tile.agg, tile.metric),
                    "descending": True,
                    "limit": tile.top,
                },
            )
    plan = ResearchPlan(steps=steps, result_step=steps[-1].step_id)
    return None if validate_plan(plan, known_steps=start) else plan


def _fits_table(tile: OutlineTile, kinds: dict[str, str]) -> bool:
    named = [tile.metric, tile.by, tile.date_column, tile.filter_column]
    if any(name is not None and name not in kinds for name in named):
        return False
    if tile.metric is None and tile.agg != "COUNT":
        return False
    if tile.agg in NUMERIC_AGGS and kinds.get(tile.metric) != "numeric":
        return False
    if tile.period is not None and kinds.get(tile.date_column) != "temporal":
        return False
    return not (tile.filter_column is not None and tile.filter_value is None)
