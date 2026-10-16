"""Comparison of products or projects across common metrics."""

from app.core.tools.base import BaseTool, identifier
from app.core.tools.domain._common import required


class ProductPerformanceTool(BaseTool):
    name = "product_performance"
    description = "Сравнение товаров/проектов по сумме, среднему и распределению метрики"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        required(columns, 2, 2, self.name)
        metric_col, group_col = columns
        return db.save_step_result(
            input_table,
            f"SELECT {identifier(group_col)} AS segment, "
            f"COUNT(*) AS observations, SUM({identifier(metric_col)}) AS total, "
            f"AVG({identifier(metric_col)}) AS average, "
            f"quantile_cont({identifier(metric_col)}, 0.5) AS median, "
            f"MIN({identifier(metric_col)}) AS minimum, "
            f"MAX({identifier(metric_col)}) AS maximum "
            f"FROM {identifier(input_table)} GROUP BY {identifier(group_col)} "
            "ORDER BY total DESC",
        )
