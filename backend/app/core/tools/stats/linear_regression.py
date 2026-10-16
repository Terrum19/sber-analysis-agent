"""Linear regression tool."""

import statistics

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class LinearRegressionTool(BaseTool):
    name = "linear_regression"
    description = "Линейная регрессия y ~ x с коэффициентами"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("linear_regression requires [y_column, x_column]")
        ys, xs = values(db, input_table, columns[0]), values(db, input_table, columns[1])
        pairs = list(zip(xs, ys))
        if len(pairs) < 2:
            slope = intercept = r_squared = None
        else:
            x_mean, y_mean = statistics.fmean(xs), statistics.fmean(ys)
            denominator = sum((x - x_mean) ** 2 for x in xs)
            slope = (
                sum((x - x_mean) * (y - y_mean) for x, y in pairs) / denominator
                if denominator
                else None
            )
            intercept = y_mean - slope * x_mean if slope is not None else None
            r_squared = statistics.correlation(xs, ys) ** 2 if slope is not None else None
        return result(db, input_table, "slope, intercept, r_squared", (slope, intercept, r_squared))
