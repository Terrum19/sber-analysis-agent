"""Revenue summary, dynamics and optional segmentation."""

from app.core.tools.base import BaseTool, identifier
from app.core.tools.domain._common import required


class RevenueAnalysisTool(BaseTool):
    name = "revenue_analysis"
    description = (
        "Сумма, среднее, медиана, динамика и сегментация выручки; "
        "parameters: date_col, group_col, interval"
    )

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        required(columns, 1, 3, self.name)
        revenue_col = columns[0]
        date_col = kwargs.get("date_col", columns[1] if len(columns) >= 2 else None)
        group_col = kwargs.get("group_col", columns[2] if len(columns) == 3 else None)
        if date_col is not None:
            identifier(str(date_col))
        if group_col is not None:
            identifier(str(group_col))
        dimensions = []
        groups = []
        order = []
        if date_col:
            interval = str(kwargs.get("interval", "month")).lower()
            if interval not in {"month", "quarter", "week", "day"}:
                raise ValueError("revenue_analysis interval must be month, quarter, week or day")
            dimensions.append(f"DATE_TRUNC('{interval}', {identifier(str(date_col))}) AS period")
            groups.append("period")
            order.append("period")
        if group_col:
            dimensions.append(f"{identifier(group_col)} AS segment")
            groups.append("segment")
            order.append("segment")
        dimensions_sql = ", ".join(dimensions) + ", " if dimensions else ""
        group_sql = f" GROUP BY {', '.join(groups)}" if groups else ""
        order_sql = f" ORDER BY {', '.join(order)}" if order else ""
        return db.save_step_result(
            input_table,
            f"SELECT {dimensions_sql}"
            f"SUM({identifier(revenue_col)}) AS revenue_sum, "
            f"AVG({identifier(revenue_col)}) AS revenue_avg, "
            f"MEDIAN({identifier(revenue_col)}) AS revenue_median, "
            f"COUNT(*) AS transaction_count "
            f"FROM {identifier(input_table)}{group_sql}{order_sql}",
        )
