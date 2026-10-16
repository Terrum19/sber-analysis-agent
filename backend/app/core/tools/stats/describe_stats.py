"""Descriptive statistics tool."""

import statistics

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class DescribeStatsTool(BaseTool):
    name = "describe_stats"
    description = "Описательная статистика: n, mean, std, min, max, q1, q3, skew, kurtosis"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 1:
            raise ValueError("describe_stats requires [column]")
        items = values(db, input_table, columns[0])
        if not items:
            stats = (0, None, None, None, None, None, None, None, None)
        else:
            ordered = sorted(items)
            mean = statistics.fmean(items)
            std = statistics.stdev(items) if len(items) > 1 else 0.0
            q1, q3 = (
                statistics.quantiles(ordered, n=4, method="inclusive")[0::2]
                if len(items) > 1
                else (items[0], items[0])
            )
            centered = [value - mean for value in items]
            m2 = statistics.fmean(value * value for value in centered)
            m3 = statistics.fmean(value**3 for value in centered)
            m4 = statistics.fmean(value**4 for value in centered)
            skew = m3 / m2**1.5 if m2 else 0.0
            kurtosis = m4 / m2**2 - 3 if m2 else 0.0
            stats = (len(items), mean, std, min(items), max(items), q1, q3, skew, kurtosis)
        return result(
            db,
            input_table,
            "n, mean, std, min, max, q1, q3, skew, kurtosis",
            stats,
        )
