"""Independent two-sample t-test tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._args import TwoSamplesArgs
from app.core.tools.stats._common import result, two_samples


class IndependentTTestTool(BaseTool):
    name = "t_test_ind"
    description = (
        "Welch t-тест независимых выборок: column_a/column_b — две числовые колонки "
        "или value_column + колонка с двумя группами (statistic, p_value, degrees_of_freedom)"
    )
    kind = "analysis"
    Args = TwoSamplesArgs

    def execute(self, db, input_table: str, args: TwoSamplesArgs) -> str:
        first, second = two_samples(db, input_table, args.column_a, args.column_b, args.mode)
        statistic = p_value = degrees_of_freedom = None
        if first and second:
            test = stats.ttest_ind(np.asarray(first), np.asarray(second), equal_var=False)
            statistic, p_value = float(test.statistic), float(test.pvalue)
            degrees_of_freedom = float(test.df)
        return result(
            db,
            input_table,
            "statistic, p_value, degrees_of_freedom",
            (statistic, p_value, degrees_of_freedom),
        )
