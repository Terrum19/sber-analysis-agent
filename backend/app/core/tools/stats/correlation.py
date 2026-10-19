"""Стат-тул: корреляция между двумя колонками.

Пример сценария: «Изменения в поле C предшествуют росту D на 5 дней?»
(вместе с timeseries/lag.py — поиск лагов и причинных связей).
"""

from app.core.tools.base import BaseTool, identifier


class CorrelationTool(BaseTool):
    """Корреляция Пирсона между двумя числовыми колонками."""

    name = "correlation"
    description = "Посчитать корреляцию между двумя числовыми колонками"
    transforms_data = False

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) != 2:
            raise ValueError("correlation requires [column_a, column_b]")
        col_a, col_b = identifier(columns[0]), identifier(columns[1])
        sql = f"SELECT corr({col_a}, {col_b}) AS correlation FROM {identifier(input_table)}"
        return db.save_step_result(input_table, sql)
