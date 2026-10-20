"""Pearson correlation tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._args import TwoColumnsArgs
from app.core.tools.stats._common import paired_values, result


class PearsonCorrelationTool(BaseTool):
    name = "pearson_corr"
    description = "Корреляция Пирсона двух числовых колонок: correlation, p_value, n"
    kind = "analysis"
    Args = TwoColumnsArgs

    def execute(self, db, input_table: str, args: TwoColumnsArgs) -> str:
        first, second = paired_values(db, input_table, args.column_a, args.column_b)
        if len(first) < 3 or np.ptp(np.asarray(first)) == 0 or np.ptp(np.asarray(second)) == 0:
            correlation = p_value = None
        else:
            test = stats.pearsonr(np.asarray(first), np.asarray(second))
            correlation, p_value = float(test.statistic), float(test.pvalue)
        return result(
            db, input_table, "correlation, p_value, n", (correlation, p_value, len(first))
        )
