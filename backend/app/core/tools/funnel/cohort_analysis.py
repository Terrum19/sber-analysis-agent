"""Cohort aggregation by cohort and analysis period."""

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier


class CohortArgs(ToolArgs):
    cohort_column: Ident
    period_column: Ident
    metric_column: Ident


class CohortAnalysisTool(BaseTool):
    name = "cohort_analysis"
    description = "Сумма/среднее метрики по когортам и периодам"
    Args = CohortArgs

    def execute(self, db, input_table: str, args: CohortArgs) -> str:
        cohort_col, period_col, metric_col = (
            args.cohort_column,
            args.period_column,
            args.metric_column,
        )
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
