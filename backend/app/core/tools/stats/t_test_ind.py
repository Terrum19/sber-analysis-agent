"""Independent two-sample t-test tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool, identifier
from app.core.tools.stats._common import result, values


class IndependentTTestTool(BaseTool):
    name = "t_test_ind"
    description = (
        "Независимый Welch t-тест: columns=[value_a, value_b] "
        "или columns=[value_column, group_column] для двух групп"
    )

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError(
                "t_test_ind requires [value_a, value_b] or [value_column, group_column]"
            )
        first = values(db, input_table, columns[0])
        statistic_result: object
        p_value: object
        degrees_of_freedom: object
        try:
            second = values(db, input_table, columns[1])
        except (TypeError, ValueError):
            second = []
        if first and second:
            statistic_result = stats.ttest_ind(
                np.asarray(first), np.asarray(second), equal_var=False
            ).statistic
            p_value = stats.ttest_ind(np.asarray(first), np.asarray(second), equal_var=False).pvalue
            degrees_of_freedom = None
        else:
            rows = db.query(
                f"SELECT {identifier(columns[0])} AS value, "
                f"{identifier(columns[1])} AS group_name "
                f"FROM {identifier(input_table)} "
                f"WHERE {identifier(columns[0])} IS NOT NULL "
                f"AND {identifier(columns[1])} IS NOT NULL"
            )
            groups: dict[object, list[float]] = {}
            for row in rows:
                groups.setdefault(row["group_name"], []).append(float(row["value"]))
            if len(groups) != 2:
                raise ValueError("Grouped t_test_ind requires exactly two groups")
            test = stats.ttest_ind(*groups.values(), equal_var=False)
            statistic_result, p_value, degrees_of_freedom = (
                test.statistic,
                test.pvalue,
                test.df,
            )
        return result(
            db,
            input_table,
            "statistic, p_value, degrees_of_freedom",
            (statistic_result, p_value, degrees_of_freedom),
        )
