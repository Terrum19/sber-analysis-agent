"""Descriptive statistics tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._args import OneColumnArgs
from app.core.tools.stats._common import result, values


class DescribeStatsTool(BaseTool):
    name = "describe_stats"
    description = "Описательная статистика: n, mean, std, min, max, q1, q3, skew, kurtosis"
    kind = "analysis"
    Args = OneColumnArgs

    def execute(self, db, input_table: str, args: OneColumnArgs) -> str:
        items = values(db, input_table, args.column)
        if not items:
            result_stats = (0, None, None, None, None, None, None, None, None)
        else:
            array = np.asarray(items)
            q1, q3 = np.quantile(array, [0.25, 0.75], method="linear")
            skew = 0.0 if np.ptp(array) == 0 else stats.skew(array, bias=True)
            kurtosis = 0.0 if np.ptp(array) == 0 else stats.kurtosis(array, fisher=True, bias=True)
            result_stats = (
                len(items),
                np.mean(array),
                np.std(array, ddof=1) if len(items) > 1 else 0.0,
                np.min(array),
                np.max(array),
                q1,
                q3,
                skew,
                kurtosis,
            )
        return result(
            db,
            input_table,
            "n, mean, std, min, max, q1, q3, skew, kurtosis",
            result_stats,
        )
