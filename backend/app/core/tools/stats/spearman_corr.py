"""Spearman correlation tool."""

import statistics

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class SpearmanCorrelationTool(BaseTool):
    name = "spearman_corr"
    description = "Корреляция Спирмена между двумя numeric-колонками"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("spearman_corr requires [column_a, column_b]")
        first, second = values(db, input_table, columns[0]), values(db, input_table, columns[1])

        def ranks(items: list[float]) -> list[float]:
            order = sorted(range(len(items)), key=items.__getitem__)
            ranked = [0.0] * len(items)
            for rank, index in enumerate(order, 1):
                ranked[index] = float(rank)
            return ranked

        ranked_first, ranked_second = ranks(first), ranks(second)
        correlation = (
            statistics.correlation(ranked_first, ranked_second)
            if len(ranked_first) > 1 and len(set(ranked_first)) > 1 and len(set(ranked_second)) > 1
            else None
        )
        return result(db, input_table, "correlation", (correlation,))
