"""Paired two-sample t-test tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import alternative, paired_values, result


class PairedTTestTool(BaseTool):
    name = "t_test_dep"
    description = (
        "Зависимые выборки paired t-test: columns=[column_before, column_after]; "
        "parameters: alternative=two-sided|less|greater"
    )
    transforms_data = False

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) != 2:
            raise ValueError("t_test_dep requires [column_before, column_after]")
        before, after = paired_values(db, input_table, columns[0], columns[1])
        if len(before) != len(after) or not before:
            raise ValueError("Paired test requires equal non-empty samples")
        test = stats.ttest_rel(
            np.asarray(before), np.asarray(after), alternative=alternative(kwargs)
        )
        return result(
            db,
            input_table,
            "statistic, p_value, degrees_of_freedom",
            (test.statistic, test.pvalue, test.df),
        )
