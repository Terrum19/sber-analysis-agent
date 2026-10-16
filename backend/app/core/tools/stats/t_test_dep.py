"""Paired two-sample t-test tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class PairedTTestTool(BaseTool):
    name = "t_test_dep"
    description = "Зависимые выборки paired t-test"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("t_test_dep requires [column_before, column_after]")
        before, after = values(db, input_table, columns[0]), values(db, input_table, columns[1])
        if len(before) != len(after) or not before:
            raise ValueError("Paired test requires equal non-empty samples")
        statistic = stats.ttest_rel(np.asarray(before), np.asarray(after)).statistic
        return result(db, input_table, "statistic", (statistic,))
