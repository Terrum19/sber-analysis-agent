"""Shapiro-Wilk normality test."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._args import OneColumnArgs
from app.core.tools.stats._common import result, values


class ShapiroWilkTool(BaseTool):
    name = "shapiro_wilk"
    description = "Тест Шапиро-Уилка на нормальность (от 3 до 5000 наблюдений): statistic, p_value"
    kind = "analysis"
    Args = OneColumnArgs

    max_observations = 5000

    def execute(self, db, input_table: str, args: OneColumnArgs) -> str:
        items = values(db, input_table, args.column)
        if len(items) > self.max_observations:
            raise ValueError(
                f"shapiro_wilk supports at most {self.max_observations} observations; "
                "use ks_test for larger samples"
            )
        test_statistic = p_value = None
        if len(items) >= 3 and np.ptp(np.asarray(items)) > 0:
            test = stats.shapiro(np.asarray(items))
            test_statistic, p_value = test.statistic, test.pvalue
        return result(db, input_table, "statistic, p_value", (test_statistic, p_value))
