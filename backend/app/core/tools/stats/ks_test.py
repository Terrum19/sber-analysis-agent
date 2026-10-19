"""One-sample Kolmogorov-Smirnov normality test."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._args import OneColumnArgs
from app.core.tools.stats._common import result, values


class KSTestTool(BaseTool):
    name = "ks_test"
    description = "Тест Колмогорова-Смирнова на нормальность колонки: statistic, p_value"
    kind = "analysis"
    Args = OneColumnArgs

    def execute(self, db, input_table: str, args: OneColumnArgs) -> str:
        items = values(db, input_table, args.column)
        if len(items) < 2:
            test_statistic = p_value = None
        else:
            array = np.asarray(items)
            std = np.std(array, ddof=1)
            if std == 0:
                test_statistic = p_value = None
            else:
                test = stats.kstest(array, stats.norm(loc=np.mean(array), scale=std).cdf)
                test_statistic, p_value = test.statistic, test.pvalue
        return result(db, input_table, "statistic, p_value", (test_statistic, p_value))
