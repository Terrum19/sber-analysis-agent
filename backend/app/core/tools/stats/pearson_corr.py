"""Pearson correlation tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import paired_values, result


class PearsonCorrelationTool(BaseTool):
    name = "pearson_corr"
    description = "Корреляция Пирсона между двумя numeric-колонками с p_value"
    transforms_data = False

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) != 2:
            raise ValueError("pearson_corr requires [column_a, column_b]")
        first, second = paired_values(db, input_table, columns[0], columns[1])
        correlation = p_value = None
        if len(first) >= 2 and np.ptp(np.asarray(first)) > 0 and np.ptp(np.asarray(second)) > 0:
            test = stats.pearsonr(np.asarray(first), np.asarray(second))
            correlation, p_value = test.statistic, test.pvalue
        return result(db, input_table, "correlation, p_value", (correlation, p_value))
