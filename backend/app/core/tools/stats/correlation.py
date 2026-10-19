"""Стат-тул: корреляция между двумя колонками (SQL, без p-value).

Оставлен для совместимости; планировщику показывается pearson_corr.
"""

from app.core.tools.base import BaseTool, identifier
from app.core.tools.stats._args import TwoColumnsArgs


class CorrelationTool(BaseTool):
    """Корреляция Пирсона между двумя числовыми колонками."""

    name = "correlation"
    description = "Посчитать корреляцию между двумя числовыми колонками"
    kind = "analysis"
    Args = TwoColumnsArgs

    def execute(self, db, input_table: str, args: TwoColumnsArgs) -> str:
        sql = (
            f"SELECT corr({args.column_a}, {args.column_b}) AS correlation "
            f"FROM {identifier(input_table)}"
        )
        return db.save_step_result(input_table, sql)
