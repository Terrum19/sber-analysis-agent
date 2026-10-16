"""Pearson correlation tool."""

import math
import statistics

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class PearsonCorrelationTool(BaseTool):
    name = "pearson_corr"
    description = "Корреляция Пирсона между двумя numeric-колонками"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("pearson_corr requires [column_a, column_b]")
        first, second = values(db, input_table, columns[0]), values(db, input_table, columns[1])
        pairs = [(x, y) for x, y in zip(first, second) if math.isfinite(x) and math.isfinite(y)]
        if len(pairs) < 2:
            correlation = None
        else:
            xs, ys = zip(*pairs)
            correlation = (
                statistics.correlation(xs, ys)
                if len(set(xs)) > 1 and len(set(ys)) > 1
                else None
            )
        return result(db, input_table, "correlation", (correlation,))
