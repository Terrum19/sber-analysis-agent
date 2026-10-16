"""Mann-Whitney U test tool."""

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class MannWhitneyUTool(BaseTool):
    name = "mann_whitney_u"
    description = "Непараметрический тест Манна-Уитни"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("mann_whitney_u requires [column_group1, column_group2]")
        first, second = values(db, input_table, columns[0]), values(db, input_table, columns[1])
        if not first or not second:
            statistic = None
        else:
            statistic = sum(1 for left in first for right in second if left > right) + 0.5 * sum(
                1 for left in first for right in second if left == right
            )
        return result(db, input_table, "statistic", (statistic,))
