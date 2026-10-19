"""One-way ANOVA tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier
from app.core.tools.stats._common import result


class AnovaArgs(ToolArgs):
    value_column: Ident
    group_column: Ident


class AnovaOneWayTool(BaseTool):
    name = "anova_one_way"
    description = "Однофакторный ANOVA: различаются ли средние по группам (statistic, p_value)"
    kind = "analysis"
    Args = AnovaArgs

    def execute(self, db, input_table: str, args: AnovaArgs) -> str:
        rows = db.query(
            f"SELECT {args.value_column} AS value, {args.group_column} AS group_name "
            f"FROM {identifier(input_table)} WHERE {args.value_column} IS NOT NULL"
        )
        groups: dict[object, list[float]] = {}
        for row in rows:
            groups.setdefault(row["group_name"], []).append(float(row["value"]))
        statistic = p_value = None
        if len(groups) > 1:
            test = stats.f_oneway(*(np.asarray(group) for group in groups.values()))
            statistic, p_value = float(test.statistic), float(test.pvalue)
        return result(
            db, input_table, "statistic, p_value, groups", (statistic, p_value, len(groups))
        )
