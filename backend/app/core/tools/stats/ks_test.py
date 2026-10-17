"""One-sample Kolmogorov-Smirnov normality test."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class KSTestTool(BaseTool):
    name = "ks_test"
    description = "Одновыборочный тест Колмогорова-Смирнова на нормальность"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 1:
            raise ValueError("ks_test requires [column]")
        items = values(db, input_table, columns[0])
        if len(items) < 2:
            test_statistic = p_value = None
        else:
            array = np.asarray(items)
            std = np.std(array, ddof=1)
            if std == 0:
                test_statistic = p_value = None
            else:
                test = stats.kstest(array, "norm", args=(np.mean(array), std))
                test_statistic, p_value = test.statistic, test.pvalue
        return result(db, input_table, "statistic, p_value", (test_statistic, p_value))
