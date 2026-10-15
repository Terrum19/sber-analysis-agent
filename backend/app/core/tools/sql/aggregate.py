"""SQL-тул: агрегация (GROUP BY) по одной колонке.

Пример сценария: «Какие значения встречаются чаще всего?»,
«Средний X по категории Y».
"""

from app.core.tools.base import BaseTool


class AggregateTool(BaseTool):
    """Группировка по колонке с COUNT/AVG/SUM."""

    name = "aggregate"
    description = "Сгруппировать по колонке и посчитать количество/среднее/сумму"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        col = columns[0]
        sql = f"SELECT {col}, COUNT(*) AS cnt, AVG(1) AS dummy FROM {input_table} GROUP BY {col} ORDER BY cnt DESC"
        return db.save_step_result(input_table, sql)
