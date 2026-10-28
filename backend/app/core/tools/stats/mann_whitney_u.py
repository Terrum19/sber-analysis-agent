"""Mann-Whitney U test tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._args import TwoSamplesArgs
from app.core.tools.stats._common import result, two_samples


class MannWhitneyUTool(BaseTool):
    name = "mann_whitney_u"
    description = (
        "Непараметрический тест Манна-Уитни; аргументы как у t_test_ind (statistic, p_value)"
    )
    kind = "analysis"
    Args = TwoSamplesArgs

    def execute(self, db, input_table: str, args: TwoSamplesArgs) -> str:
        first, second = two_samples(db, input_table, args.column_a, args.column_b, args.mode)
        statistic = p_value = None
        if first and second:
            test = stats.mannwhitneyu(
                np.asarray(first),
                np.asarray(second),
                method="auto",
                alternative=args.alternative,
            )
            statistic, p_value = float(test.statistic), float(test.pvalue)
        return result(db, input_table, "statistic, p_value", (statistic, p_value))
