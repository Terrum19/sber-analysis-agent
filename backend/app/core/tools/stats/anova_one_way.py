"""One-way ANOVA tool."""

import statistics

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
        all_values = [item for group in groups.values() for item in group]
        grand_mean = statistics.fmean(all_values) if all_values else 0
        between = sum(
            len(group) * (statistics.fmean(group) - grand_mean) ** 2 for group in groups.values()
        )
        within = sum(
            sum((value - statistics.fmean(group)) ** 2 for value in group)
            for group in groups.values()
        )
        statistic = (
            (between / (len(groups) - 1)) / (within / (len(all_values) - len(groups)))
            if len(groups) > 1 and len(all_values) > len(groups) and within
            else None
        )
        return result(db, input_table, "statistic", (statistic,))
