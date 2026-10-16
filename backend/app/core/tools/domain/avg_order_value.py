"""Average order value with optional segmentation."""

from app.core.tools.base import BaseTool, identifier
from app.core.tools.domain._common import required


class AverageOrderValueTool(BaseTool):
    name = "avg_order_value"
    description = "Средний чек с опциональной сегментацией"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        required(columns, 1, 2, self.name)
        order_col = columns[0]
        group_col = columns[1] if len(columns) == 2 else None
        prefix = f"{identifier(group_col)} AS segment, " if group_col else ""
        group_sql = f" GROUP BY {identifier(group_col)}" if group_col else ""
        order_sql = " ORDER BY segment" if group_col else ""
        return db.save_step_result(
            input_table,
            f"SELECT {prefix}AVG({identifier(order_col)}) AS avg_order_value, "
            f"COUNT(*) AS order_count, SUM({identifier(order_col)}) AS total_value "
            f"FROM {identifier(input_table)}{group_sql}{order_sql}",
        )
