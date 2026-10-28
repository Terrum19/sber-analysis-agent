"""Paired two-sample t-test tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool, Ident, ToolArgs
from app.core.tools.stats._args import Alternative
from app.core.tools.stats._common import paired_values, result


class PairedTTestArgs(ToolArgs):
    column_before: Ident
    column_after: Ident
    alternative: Alternative = "two-sided"


class PairedTTestTool(BaseTool):
    name = "t_test_dep"
    description = "Парный t-тест (до/после в одной строке): statistic, p_value, n"
    kind = "analysis"
    Args = PairedTTestArgs

    def execute(self, db, input_table: str, args: PairedTTestArgs) -> str:
        before, after = paired_values(db, input_table, args.column_before, args.column_after)
        if len(before) < 2:
            raise ValueError("Paired test requires at least two complete pairs")
        test = stats.ttest_rel(np.asarray(before), np.asarray(after), alternative=args.alternative)
        return result(
            db,
            input_table,
            "statistic, p_value, degrees_of_freedom, n",
            (test.statistic, test.pvalue, test.df, len(before)),
        )
