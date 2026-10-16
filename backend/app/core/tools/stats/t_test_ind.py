"""Independent two-sample t-test tool."""

import math
import statistics

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class IndependentTTestTool(BaseTool):
    name = "t_test_ind"
    description = "Независимые выборки t-Student"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("t_test_ind requires [column_group1, column_group2]")
        first, second = values(db, input_table, columns[0]), values(db, input_table, columns[1])
        if not first or not second:
            statistic = None
        else:
            mean_a, mean_b = statistics.fmean(first), statistics.fmean(second)
            var_a = statistics.variance(first) if len(first) > 1 else 0
            var_b = statistics.variance(second) if len(second) > 1 else 0
            denominator = math.sqrt(var_a / len(first) + var_b / len(second))
            statistic = (mean_a - mean_b) / denominator if denominator else None
        return result(db, input_table, "statistic", (statistic,))
