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
        "или value_column + колонка ровно с двумя группами (для трёх и более — anova_one_way); "
        "statistic, p_value, degrees_of_freedom"
    )
    kind = "analysis"
    Args = TwoSamplesArgs

    def execute(self, db, input_table: str, args: TwoSamplesArgs) -> str:
        first, second = two_samples(db, input_table, args.column_a, args.column_b, args.mode)
        if len(first) < 2 or len(second) < 2:
            raise ValueError("t_test_ind requires at least two observations in each sample")
        test = stats.ttest_ind(
            np.asarray(first),
            np.asarray(second),
            equal_var=False,
            alternative=args.alternative,
        )
        return result(
            db,
            input_table,
            "statistic, p_value, degrees_of_freedom",
            (test.statistic, test.pvalue, test.df),
        )
