"""SQL-тул: агрегация (GROUP BY) по одной колонке.

Пример сценария: «Какие значения встречаются чаще всего?»,
«Средний X по категории Y».
"""

from app.core.tools.base import BaseTool, identifier


class AggregateTool(BaseTool):
    """Группировка по колонке с COUNT/AVG/SUM."""

    name = "aggregate"
    description = "Сгруппировать по колонке и посчитать количество/среднее/сумму"

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if not columns:
            raise ValueError("aggregate requires [column]")
        col = identifier(columns[0])
        sql = f"SELECT {col}, COUNT(*) AS cnt FROM {identifier(input_table)} GROUP BY {col} ORDER BY cnt DESC"
        return db.save_step_result(input_table, sql)
