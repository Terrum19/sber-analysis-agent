"""Conversion funnel by ordered event steps."""

from app.core.tools.base import BaseTool, identifier
from app.core.tools.funnel._common import columns, rows_result


class FunnelStepsTool(BaseTool):
    name = "funnel_steps"
    description = "Воронка конверсии по шагам с конверсиями между шагами"

    def run(self, db, input_table: str, columns_: list[str]) -> str:
        columns(columns_, 2, 3, self.name)
        step_col, order_col = columns_[:2]
        user_col = columns_[2] if len(columns_) == 3 else None
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
