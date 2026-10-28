"""Semantic intent -> ResearchPlan/SQL (P1)."""

from __future__ import annotations

from app.core.data.semantic_planner import SemanticIntent
from app.core.tools.base import identifier, literal
from app.models.research_step import ResearchPlan, ResearchStep
from app.models.semantic import SemanticMetric, SemanticModel


class SemanticCompiler:
    """Компилятор управляемой семантики в обычный план тулов."""

    def compile(self, intent: SemanticIntent, model: SemanticModel) -> ResearchPlan:
        metrics = [
            metric for metric in intent.metrics if metric.column or metric.aggregation == "COUNT"
        ]
        if not metrics:
            raise ValueError("в вопросе не найдено ни одной метрики")
        steps: list[ResearchStep] = []
        temporal = next(
            (dimension for dimension in intent.dimensions if dimension.kind == "temporal"), None
        )
        if temporal is not None and intent.time_range:
            start, end = intent.time_range
            steps.append(
                ResearchStep(
                    step_id=0,
                    tool="filter_rows",
                    input="$source",
                    arguments={
                        "column": temporal.column,
                        "operator": "BETWEEN",
                        "value": [start, end],
                    },
                    purpose="Период",
                )
            )
        for index, condition in enumerate(intent.filters, len(steps)):
            steps.append(
                ResearchStep(
                    step_id=index,
                    tool="filter_rows",
                    input="$source" if index == 0 else "$prev",
                    arguments=condition,
                    purpose=f"Фильтр {condition.get('column')}",
                )
            )
        next_id = len(steps)
        temporal = next(
            (dimension for dimension in intent.dimensions if dimension.kind == "temporal"), None
        )
        others = [dimension for dimension in intent.dimensions if dimension.kind != "temporal"]
        if temporal is not None and intent.time_grain and len(metrics) == 1:
            metric = metrics[0]
            arguments = {
                "date_column": temporal.column,
                "interval": intent.time_grain,
                "function": metric.aggregation,
                "by": [dimension.column for dimension in others],
            }
            if metric.column:
                arguments["metric_column"] = metric.column
            steps.append(
                ResearchStep(
                    step_id=next_id,
                    tool="time_bucket",
                    input="$prev" if steps else "$source",
                    arguments=arguments,
                    purpose=f"{metric.title} по {intent.time_grain}",
                )
            )
            return ResearchPlan(steps=steps, final_viz="line")
        by = [dimension.column for dimension in others]
        if temporal is not None:
            by.append(temporal.column)
        arguments: dict[str, object] = {"by": by, "metrics": _metric_specs(metrics)}
        steps.append(
            ResearchStep(
                step_id=next_id,
                tool="group_by",
                input="$prev" if steps else "$source",
                arguments=arguments,
                purpose=", ".join(metric.title for metric in metrics),
            )
        )
        final_viz = "kpi" if not by else "bar"
        return ResearchPlan(steps=steps, final_viz=final_viz)

    def to_sql(self, intent: SemanticIntent, model: SemanticModel) -> str:
        """SQL для dry-run: только чтение, без записи."""
        metrics = [
            metric for metric in intent.metrics if metric.column or metric.aggregation == "COUNT"
        ]
        if not metrics:
            raise ValueError("в вопросе не найдено ни одной метрики")
        select = [_metric_sql(metric) for metric in metrics]
        temporal = next(
            (dimension for dimension in intent.dimensions if dimension.kind == "temporal"), None
        )
        group: list[str] = []
        if temporal is not None and intent.time_grain:
            select.insert(
                0,
                f"date_trunc('{intent.time_grain}', {identifier(temporal.column)}) AS period",
            )
            group.append("period")
        for dimension in intent.dimensions:
            if dimension is temporal and intent.time_grain:
                continue
            alias = identifier(dimension.column)
            select.append(alias)
            group.append(alias)
        sql = f"SELECT {', '.join(select)} FROM {identifier(model.table)}"
        conditions = []
        if temporal is not None and intent.time_range:
            start, end = intent.time_range
            conditions.append(
                f"{identifier(temporal.column)} BETWEEN {literal(start)} AND {literal(end)}"
            )
        for condition in intent.filters:
            column = identifier(str(condition.get("column", "")))
            operator = str(condition.get("operator", "="))
            value = condition.get("value")
            if operator in ("IN", "NOT IN"):
                values = ", ".join(literal(item) for item in value or [])
                conditions.append(f"{column} {operator} ({values})")
            elif operator == "BETWEEN":
                low, high = value  # type: ignore[misc]
                conditions.append(f"{column} BETWEEN {literal(low)} AND {literal(high)}")
            else:
                conditions.append(f"{column} {operator} {literal(value)}")
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        if group:
            sql += " GROUP BY " + ", ".join(group)
            if temporal is not None and intent.time_grain:
                sql += " ORDER BY period"
        return sql


def _metric_specs(metrics: list[SemanticMetric]) -> list[dict[str, str]]:
    specs = []
    for metric in metrics:
        spec = {"function": metric.aggregation, "alias": metric.name}
        if metric.column:
            spec["column"] = metric.column
        specs.append(spec)
    return specs


def _metric_sql(metric: SemanticMetric) -> str:
    if metric.sql:
        return f"({metric.sql}) AS {identifier(metric.name)}"
    function = metric.aggregation
    if function == "COUNT" and not metric.column:
        expression = "COUNT(*)"
    elif function == "COUNT_DISTINCT":
        expression = f"COUNT(DISTINCT {identifier(metric.column or '')})"
    elif function == "MEDIAN":
        expression = f"MEDIAN({identifier(metric.column or '')})"
    else:
        expression = f"{function}({identifier(metric.column or '')})"
    return f"{expression} AS {identifier(metric.name)}"
