"""Mann-Whitney U test tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import alternative, result, values


class MannWhitneyUTool(BaseTool):
    name = "mann_whitney_u"
    description = (
        "Непараметрический тест Манна-Уитни: columns=[column_group1, column_group2]; "
        "parameters: alternative=two-sided|less|greater"
    )
    transforms_data = False

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) != 2:
            raise ValueError("mann_whitney_u requires [column_group1, column_group2]")
        first, second = values(db, input_table, columns[0]), values(db, input_table, columns[1])
        statistic = p_value = None
        if first and second:
            test = stats.mannwhitneyu(
                np.asarray(first),
                np.asarray(second),
                method="auto",
                alternative=alternative(kwargs),
            )
            statistic, p_value = test.statistic, test.pvalue
        return result(db, input_table, "statistic, p_value", (statistic, p_value))
