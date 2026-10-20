"""Revenue summary, dynamics and optional segmentation."""

from pydantic import model_validator

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier
from app.core.tools.sql.atomic import Interval


class RevenueAnalysisArgs(ToolArgs):
    revenue_column: Ident
    date_column: Ident | None = None
    group_column: Ident | None = None
    interval: Interval = "month"

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict) and isinstance(data.get("interval"), str):
            data["interval"] = data["interval"].lower()
        return data


class RevenueAnalysisTool(BaseTool):
    name = "revenue_analysis"
    description = (
        "Сумма, среднее, медиана и число транзакций выручки; date_column даёт динамику "
        "по interval (period), group_column — сегменты (segment)"
    )
    Args = RevenueAnalysisArgs

    def execute(self, db, input_table: str, args: RevenueAnalysisArgs) -> str:
        revenue = identifier(args.revenue_column)
        dimensions, groups = [], []
        if args.date_column:
            dimensions.append(
                f"DATE_TRUNC('{args.interval}', {identifier(args.date_column)}) AS period"
            )
            groups.append("period")
        if args.group_column:
            dimensions.append(f"{identifier(args.group_column)} AS segment")
            groups.append("segment")
        dimensions_sql = ", ".join(dimensions) + ", " if dimensions else ""
        group_sql = f" GROUP BY {', '.join(groups)} ORDER BY {', '.join(groups)}" if groups else ""
        return db.save_step_result(
            input_table,
            f"SELECT {dimensions_sql}"
            f"SUM({revenue}) AS revenue_sum, "
            f"AVG({revenue}) AS revenue_avg, "
            f"MEDIAN({revenue}) AS revenue_median, "
            f"COUNT(*) AS transaction_count "
            f"FROM {identifier(input_table)}{group_sql}",
        )
