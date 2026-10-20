"""Comparison of products or projects across common metrics.

Оставлен для совместимости; планировщику показывается group_by с metrics.
"""

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier


class ProductPerformanceArgs(ToolArgs):
    metric_column: Ident
    group_column: Ident


class ProductPerformanceTool(BaseTool):
    name = "product_performance"
    description = "Сравнение товаров/проектов по сумме, среднему и распределению метрики"
    Args = ProductPerformanceArgs

    def execute(self, db, input_table: str, args: ProductPerformanceArgs) -> str:
        metric, group = identifier(args.metric_column), identifier(args.group_column)
        return db.save_step_result(
            input_table,
            f"SELECT {group} AS segment, "
            f"COUNT(*) AS observations, SUM({metric}) AS total, "
            f"AVG({metric}) AS average, "
            f"quantile_cont({metric}, 0.5) AS median, "
            f"MIN({metric}) AS minimum, "
            f"MAX({metric}) AS maximum "
            f"FROM {identifier(input_table)} GROUP BY {group} "
            "ORDER BY total DESC",
        )
