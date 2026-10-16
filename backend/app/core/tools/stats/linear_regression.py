"""Linear regression tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import result, values


class LinearRegressionTool(BaseTool):
    name = "linear_regression"
    description = "Линейная регрессия y ~ x с коэффициентами"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("linear_regression requires [y_column, x_column]")
        ys, xs = values(db, input_table, columns[0]), values(db, input_table, columns[1])
        if len(xs) < 2 or np.ptp(np.asarray(xs)) == 0:
            slope = intercept = r_squared = None
        else:
            fit = stats.linregress(np.asarray(xs), np.asarray(ys))
            slope, intercept, r_squared = fit.slope, fit.intercept, fit.rvalue**2
        return result(db, input_table, "slope, intercept, r_squared", (slope, intercept, r_squared))
