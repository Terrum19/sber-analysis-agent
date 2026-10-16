"""Paired two-sample t-test tool."""

import math
import statistics

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class PairedTTestTool(BaseTool):
    name = "t_test_dep"
    description = "Зависимые выборки paired t-test"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("t_test_dep requires [column_before, column_after]")
        before, after = values(db, input_table, columns[0]), values(db, input_table, columns[1])
        if len(before) != len(after) or not before:
            raise ValueError("Paired test requires equal non-empty samples")
        differences = [a - b for a, b in zip(before, after)]
        mean = statistics.fmean(differences)
        std = statistics.stdev(differences) if len(differences) > 1 else 0
        statistic = mean / (std / math.sqrt(len(differences))) if std else None
        return result(db, input_table, "statistic", (statistic,))
