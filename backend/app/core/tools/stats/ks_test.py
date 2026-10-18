"""One-sample Kolmogorov-Smirnov normality test."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class KSTestTool(BaseTool):
    name = "ks_test"
    description = (
        "Одновыборочный тест Колмогорова-Смирнова на нормальность; параметры "
        "нормального распределения оцениваются по выборке, поэтому p_value "
        "завышен (для проверки нормальности точнее shapiro_wilk)"
    )
    transforms_data = False

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) != 1:
            raise ValueError("ks_test requires [column]")
        items = values(db, input_table, columns[0])
        test_statistic = p_value = None
        if len(items) >= 2:
            array = np.asarray(items)
            std = np.std(array, ddof=1)
            if std > 0:
                test = stats.kstest(array, stats.norm(loc=np.mean(array), scale=std).cdf)
                test_statistic, p_value = test.statistic, test.pvalue
        return result(db, input_table, "statistic, p_value", (test_statistic, p_value))
