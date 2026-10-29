"""Быстрые плитки дашборда (#224, варианты quick_tiles и rich_tiles): шаги из полей плана.

Простая плитка — «агрегат показателя в разрезе по периодам»: план дашборда
отдаёт её поля (metric, agg, by, period, date_column, top, фильтр), а шаги
group_by / time_bucket / sort_limit / filter_rows собирает этот модуль.

Вариант rich_tiles: показатель может быть формулой (expression -> шаг
create_column, metric — имя новой колонки), а рядом с ним — ещё 1–2
показателя (extra_metrics). Несколько показателей по периодам считаются
group_by по колонке периода, которую добавляет create_column с date_trunc.

Поля не сходятся с таблицей — None: плитку строит обычный планировщик.
"""

from app.core.agent.planner import validate_plan
from app.core.tools.base import identifier
from app.core.tools.sql.atomic import aggregate_alias
from app.models.dashboard_plan import MAX_EXTRA_METRICS, OutlineTile, TileMetric
from app.models.research_step import PREVIOUS, SOURCE, ResearchPlan, ResearchStep

# Агрегаты, которым нужна числовая колонка.
NUMERIC_AGGS = {"SUM", "AVG", "MIN", "MAX", "MEDIAN", "STDDEV"}
# Колонка периода, как у time_bucket.
PERIOD_COLUMN = "period"


def quick_plan(tile: OutlineTile, kinds: dict[str, str], start: int) -> ResearchPlan | None:
    """План простой плитки по её полям; kinds — колонка -> вид из паспорта таблицы.

    None — плитка сложная или поля не подходят к колонкам таблицы.
    """
    if tile.kind != "simple" or tile.agg is None:
        return None
    main = TileMetric(metric=tile.metric, agg=tile.agg, expression=tile.expression)
    metrics = [main, *tile.extra_metrics[:MAX_EXTRA_METRICS]]
    if not _fits_table(tile, metrics, kinds):
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
    for item in metrics:
        if item.expression is not None:
            add("create_column", {"new_column": item.metric, "expression": item.expression})
    by = [tile.by] if tile.by else []
    if len(metrics) == 1 and tile.period is not None:
        add(
            "time_bucket",
            {
                "date_column": tile.date_column,
                "interval": tile.period,
                "metric_column": tile.metric,
                "function": tile.agg,
                "by": by,
            },
        )
    else:
        if tile.period is not None:
            truncated = f"date_trunc('{tile.period}', {identifier(tile.date_column)})"
            add("create_column", {"new_column": PERIOD_COLUMN, "expression": truncated})
            by = [PERIOD_COLUMN, *by]
        add(
            "group_by",
            {
                "by": by,
                "metrics": [{"function": item.agg, "column": item.metric} for item in metrics],
            },
        )
        if tile.by and tile.top and tile.period is None:
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


def _fits_table(tile: OutlineTile, metrics: list[TileMetric], kinds: dict[str, str]) -> bool:
    named = [tile.by, tile.date_column, tile.filter_column]
    if any(name is not None and name not in kinds for name in named):
        return False
    created = {item.metric for item in metrics if item.expression is not None}
    if PERIOD_COLUMN in kinds and tile.period is not None and len(metrics) > 1:
        return False
    for item in metrics:
        if item.expression is not None:
            # Формула — новая колонка: имя обязательно и не совпадает с колонкой таблицы.
            if item.metric is None or item.metric in kinds:
                return False
            continue
        if item.metric in created:
            # Показатель по формуле, которую задал другой показатель плитки.
            continue
        if item.metric is not None and item.metric not in kinds:
            return False
        if item.metric is None and item.agg != "COUNT":
            return False
        if item.agg in NUMERIC_AGGS and kinds.get(item.metric) != "numeric":
            return False
    if tile.period is not None and kinds.get(tile.date_column) != "temporal":
        return False
    return not (tile.filter_column is not None and tile.filter_value is None)
