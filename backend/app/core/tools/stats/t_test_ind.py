"""Independent two-sample t-test tool."""

import numpy as np
from scipy import stats

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
            statistic = stats.ttest_ind(
                np.asarray(first), np.asarray(second), equal_var=False
            ).statistic
        return result(db, input_table, "statistic", (statistic,))
