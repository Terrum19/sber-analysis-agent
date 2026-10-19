"""Conversion funnel by ordered event steps."""

from app.core.tools.base import BaseTool, Ident, identifier
from app.core.tools.funnel._common import StepOrderArgs, rows_result


class FunnelStepsArgs(StepOrderArgs):
    user_column: Ident | None = None


class FunnelStepsTool(BaseTool):
    name = "funnel_steps"
    description = (
        "Воронка: число событий (или уникальных user_column) на шаг, conversion_rate "
        "от первого шага и step_conversion_rate от предыдущего"
    )
    Args = FunnelStepsArgs

    def execute(self, db, input_table: str, args: FunnelStepsArgs) -> str:
        step_col, order_col, user_col = args.step_column, args.order_column, args.user_column
        count_expr = f"COUNT(DISTINCT {identifier(user_col)})" if user_col else "COUNT(*)"
        rows = db.query(
            f"SELECT {identifier(step_col)} AS step, {count_expr} AS count, "
            f"MIN({identifier(order_col)}) AS first_order "
            f"FROM {identifier(input_table)} GROUP BY {identifier(step_col)} "
            "ORDER BY first_order, step"
        )
        if not rows:
            raise ValueError("funnel_steps requires at least one step")
        first_count = float(rows[0]["count"])
        previous = first_count
        result_rows = []
        for row in rows:
            count = float(row["count"])
            result_rows.append(
                (
                    row["step"],
                    int(count),
                    count / first_count if first_count else None,
                    count / previous if previous else None,
                )
            )
            previous = count
        return rows_result(
            db,
            input_table,
            ["step", "count", "conversion_rate", "step_conversion_rate"],
            result_rows,
        )
