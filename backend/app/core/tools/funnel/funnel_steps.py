"""Conversion funnel by ordered event steps."""

from app.core.tools.base import BaseTool
from app.core.tools.funnel._common import columns, rows_result, step_counts


class FunnelStepsTool(BaseTool):
    name = "funnel_steps"
    description = (
        "Воронка конверсии: columns=[step_col, order_col, user_id_col?]; с user_id_col "
        "пользователь засчитывается на шаге, только пройдя предыдущие шаги по порядку; "
        "parameters: steps — порядок шагов (иначе выводится из данных)"
    )

    def run(self, db, input_table: str, columns_: list[str], **kwargs: object) -> str:
        columns(columns_, 2, 3, self.name)
        step_col, order_col = columns_[:2]
        user_col = columns_[2] if len(columns_) == 3 else None
        counts = step_counts(db, input_table, step_col, order_col, user_col, kwargs.get("steps"))
        first_count = counts[0][1]
        previous = first_count
        result_rows = []
        for step, count in counts:
            result_rows.append(
                (
                    step,
                    count,
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
