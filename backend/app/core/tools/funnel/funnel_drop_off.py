"""Drop-off between consecutive funnel steps."""

from app.core.tools.base import BaseTool
from app.core.tools.funnel._common import columns, rows_result, step_counts


class FunnelDropOffTool(BaseTool):
    name = "funnel_drop_off"
    description = (
        "Потери между шагами воронки: columns=[step_col, order_col, user_id_col?]; "
        "без user_id_col считаются события, а не пользователи; "
        "parameters: steps — порядок шагов"
    )

    def run(self, db, input_table: str, columns_: list[str], **kwargs: object) -> str:
        columns(columns_, 2, 3, self.name)
        step_col, order_col = columns_[:2]
        user_col = columns_[2] if len(columns_) == 3 else None
        counts = step_counts(db, input_table, step_col, order_col, user_col, kwargs.get("steps"))
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
