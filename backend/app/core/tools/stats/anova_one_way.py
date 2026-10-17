"""One-way ANOVA tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool, identifier
from app.core.tools.stats._common import result


class AnovaOneWayTool(BaseTool):
    name = "anova_one_way"
    description = "Одноварный ANOVA для сравнения средних по группам"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("anova_one_way requires [value_column, group_column]")
        value_column, group_column = identifier(columns[0]), identifier(columns[1])
        rows = db.query(
            f"SELECT {value_column} AS value, {group_column} AS group_name "
            f"FROM {identifier(input_table)} WHERE {value_column} IS NOT NULL"
        )
        groups: dict[object, list[float]] = {}
        for row in rows:
            groups.setdefault(row["group_name"], []).append(float(row["value"]))
        statistic = (
            stats.f_oneway(*(np.asarray(group) for group in groups.values())).statistic
            if len(groups) > 1
            else None
        )
        return result(db, input_table, "statistic", (statistic,))
