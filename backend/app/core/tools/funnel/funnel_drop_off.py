"""Drop-off between consecutive funnel steps."""

from app.core.tools.base import BaseTool
from app.core.tools.funnel._common import FunnelArgs, rows_result, step_counts


class FunnelDropOffTool(BaseTool):
    name = "funnel_drop_off"
    description = (
        "Потери между шагами воронки (drop_off, drop_off_rate); без user_column "
        "считаются события, а не пользователи; steps — порядок шагов"
    )
    Args = FunnelArgs

    def execute(self, db, input_table: str, args: FunnelArgs) -> str:
        counts = step_counts(
            db, input_table, args.step_column, args.order_column, args.user_column, args.steps
        )
        result_rows = []
        previous = None
        for step, count in counts:
            drop = previous - count if previous is not None else 0
            result_rows.append((step, count, drop, drop / previous if previous else 0.0))
            previous = count
        return rows_result(
            db,
            input_table,
            ["step", "count", "drop_off", "drop_off_rate"],
            result_rows,
        )
