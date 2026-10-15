"""Стат-тул: корреляция между двумя колонками.

Пример сценария: «Изменения в поле C предшествуют росту D на 5 дней?»
(вместе с timeseries/lag.py — поиск лагов и причинных связей).
"""

from app.core.tools.base import BaseTool


class CorrelationTool(BaseTool):
    """Корреляция Пирсона между двумя числовыми колонками."""

    name = "correlation"
    description = "Посчитать корреляцию между двумя числовыми колонками"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        col_a, col_b = columns[0], columns[1]
        sql = f"SELECT corr({col_a}, {col_b}) AS correlation FROM {input_table}"
        return db.save_step_result(input_table, sql)
