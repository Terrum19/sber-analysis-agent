"""Spearman correlation tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class SpearmanCorrelationTool(BaseTool):
    name = "spearman_corr"
    description = "Корреляция Спирмена между двумя numeric-колонками"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("spearman_corr requires [column_a, column_b]")
        first, second = values(db, input_table, columns[0]), values(db, input_table, columns[1])

        if len(first) < 2 or np.ptp(np.asarray(first)) == 0 or np.ptp(np.asarray(second)) == 0:
            correlation = None
        else:
            correlation = stats.spearmanr(np.asarray(first), np.asarray(second)).statistic
        return result(db, input_table, "correlation", (correlation,))
