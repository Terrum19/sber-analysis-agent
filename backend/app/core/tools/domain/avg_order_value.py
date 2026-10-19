"""Average order value with optional segmentation."""

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier


class AverageOrderValueArgs(ToolArgs):
    order_value_column: Ident
    group_column: Ident | None = None


class AverageOrderValueTool(BaseTool):
    name = "avg_order_value"
    description = (
        "Средний чек (avg_order_value, order_count, total_value), опционально по сегментам"
    )
    Args = AverageOrderValueArgs

    def execute(self, db, input_table: str, args: AverageOrderValueArgs) -> str:
        value, group = args.order_value_column, args.group_column
        prefix = f"{group} AS segment, " if group else ""
        group_sql = f" GROUP BY {group}" if group else ""
        order_sql = " ORDER BY segment" if group else ""
        return db.save_step_result(
            input_table,
            f"SELECT {prefix}AVG({value}) AS avg_order_value, "
            f"COUNT(*) AS order_count, SUM({value}) AS total_value "
            f"FROM {identifier(input_table)}{group_sql}{order_sql}",
        )
