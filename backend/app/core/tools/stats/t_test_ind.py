"""Independent two-sample t-test tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool, identifier
from app.core.tools.stats._common import alternative, result, values

NUMERIC_TYPES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
)


class IndependentTTestTool(BaseTool):
    name = "t_test_ind"
    description = (
        "Независимый Welch t-тест двух выборок: columns=[value_a, value_b] "
        "или columns=[value_column, group_column] ровно для двух групп "
        "(для трёх и более групп — anova_one_way); "
        "parameters: mode=samples|groups, если вторая колонка числовая; "
        "alternative=two-sided|less|greater"
    )
    transforms_data = False

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) != 2:
            raise ValueError(
                "t_test_ind requires [value_a, value_b] or [value_column, group_column]"
            )
        mode = str(kwargs.get("mode") or self.detect_mode(db, input_table, columns[1])).lower()
        if mode == "samples":
            first, second = values(db, input_table, columns[0]), values(db, input_table, columns[1])
        elif mode == "groups":
            first, second = self.groups(db, input_table, columns[0], columns[1])
        else:
            raise ValueError("t_test_ind mode must be samples or groups")
        if len(first) < 2 or len(second) < 2:
            raise ValueError("t_test_ind requires at least two observations in each sample")
        test = stats.ttest_ind(
            np.asarray(first),
            np.asarray(second),
            equal_var=False,
            alternative=alternative(kwargs),
        )
        return result(
            db,
            input_table,
            "statistic, p_value, degrees_of_freedom",
            (test.statistic, test.pvalue, test.df),
        )

    @staticmethod
    def detect_mode(db, table: str, column: str) -> str:
        """Group mode for a text column or a numeric column with exactly two values."""
        row = db.query(
            f"SELECT COUNT(DISTINCT {identifier(column)}) AS levels, "
            f"any_value(typeof({identifier(column)})) AS kind "
            f"FROM {identifier(table)} WHERE {identifier(column)} IS NOT NULL"
        )[0]
        numeric = str(row["kind"] or "").upper().startswith(NUMERIC_TYPES)
        return "samples" if numeric and row["levels"] != 2 else "groups"

    @staticmethod
    def groups(db, table: str, value_column: str, group_column: str) -> list[list[float]]:
        rows = db.query(
            f"SELECT {identifier(value_column)} AS value, "
            f"{identifier(group_column)} AS group_name "
            f"FROM {identifier(table)} "
            f"WHERE {identifier(value_column)} IS NOT NULL "
            f"AND {identifier(group_column)} IS NOT NULL "
            f"ORDER BY group_name"
        )
        grouped: dict[object, list[float]] = {}
        for row in rows:
            grouped.setdefault(row["group_name"], []).append(float(row["value"]))
        if len(grouped) != 2:
            raise ValueError(
                "Grouped t_test_ind requires exactly two groups; use anova_one_way for more"
            )
        return list(grouped.values())
