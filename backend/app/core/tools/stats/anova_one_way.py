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
    description = (
        "Однофакторный ANOVA: различаются ли средние двух и более групп "
        "(statistic, p_value, df_between, df_within)"
    )
    kind = "analysis"
    Args = AnovaArgs

    def execute(self, db, input_table: str, args: AnovaArgs) -> str:
        value_column, group_column = identifier(args.value_column), identifier(args.group_column)
        rows = db.query(
            f"SELECT {value_column} AS value, {group_column} AS group_name "
            f"FROM {identifier(input_table)} WHERE {value_column} IS NOT NULL"
        )
        groups: dict[object, list[float]] = {}
        for row in rows:
            groups.setdefault(row["group_name"], []).append(float(row["value"]))
        observations = sum(len(group) for group in groups.values())
        outcome: tuple[object, ...] = (None, None, None, None)
        if len(groups) > 1 and observations > len(groups):
            test = stats.f_oneway(*(np.asarray(group) for group in groups.values()))
            outcome = (test.statistic, test.pvalue, len(groups) - 1, observations - len(groups))
        return result(
            db,
            input_table,
            "statistic, p_value, df_between, df_within, groups",
            (*outcome, len(groups)),
        )
