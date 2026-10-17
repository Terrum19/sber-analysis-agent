"""Cohort aggregation by cohort and analysis period."""

from app.core.tools.base import BaseTool, identifier
from app.core.tools.funnel._common import columns


class CohortAnalysisTool(BaseTool):
    name = "cohort_analysis"
    description = "Агрегация метрики по когортам и временным периодам"

    def run(self, db, input_table: str, columns_: list[str]) -> str:
        columns(columns_, 3, 3, self.name)
        cohort_col, period_col, metric_col = columns_
        return db.save_step_result(
            input_table,
            f"SELECT {identifier(cohort_col)} AS cohort, "
            f"{identifier(period_col)} AS period, "
            f"SUM({identifier(metric_col)}) AS metric, "
            f"AVG({identifier(metric_col)}) AS metric_avg, "
            f"COUNT(*) AS observations "
            f"FROM {identifier(input_table)} "
            f"GROUP BY {identifier(cohort_col)}, {identifier(period_col)} "
            "ORDER BY cohort, period",
        )
