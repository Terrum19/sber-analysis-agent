"""Conversion funnel by ordered event steps."""

from app.core.tools.base import BaseTool
from app.core.tools.funnel._common import FunnelArgs, rows_result, step_counts


class FunnelStepsTool(BaseTool):
    name = "funnel_steps"
    description = (
        "Воронка: число событий на шаг (с user_column — пользователей, прошедших "
        "предыдущие шаги по порядку), conversion_rate от первого шага и "
        "step_conversion_rate от предыдущего; steps — порядок шагов (иначе выводится из данных)"
    )
    Args = FunnelArgs

    def execute(self, db, input_table: str, args: FunnelArgs) -> str:
        counts = step_counts(
            db, input_table, args.step_column, args.order_column, args.user_column, args.steps
        )
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
