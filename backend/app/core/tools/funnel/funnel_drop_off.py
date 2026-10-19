"""Drop-off between consecutive funnel steps."""

from app.core.tools.base import BaseTool, identifier
from app.core.tools.funnel._common import StepOrderArgs, rows_result


class FunnelDropOffTool(BaseTool):
    name = "funnel_drop_off"
    description = (
        "Потери между шагами воронки (drop_off, drop_off_rate); шаги упорядочены "
        "по первому появлению order_column"
    )
    Args = StepOrderArgs

    def execute(self, db, input_table: str, args: StepOrderArgs) -> str:
        step_col, order_col = args.step_column, args.order_column
        rows = db.query(
            f"SELECT {identifier(step_col)} AS step, COUNT(*) AS count, "
            f"MIN({identifier(order_col)}) AS first_order "
            f"FROM {identifier(input_table)} GROUP BY {identifier(step_col)} "
            "ORDER BY first_order, step"
        )
        if not rows:
            raise ValueError("funnel_drop_off requires at least one step")
        result_rows = []
        previous = None
        for row in rows:
            count = int(row["count"])
            drop = previous - count if previous is not None else 0
            result_rows.append((row["step"], count, drop, drop / previous if previous else 0.0))
            previous = count
        return rows_result(
            db,
            input_table,
            ["step", "count", "drop_off", "drop_off_rate"],
            result_rows,
        )
