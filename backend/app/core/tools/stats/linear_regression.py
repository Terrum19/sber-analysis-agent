"""Linear regression tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool
from app.core.tools.stats._common import paired_values, result


class LinearRegressionTool(BaseTool):
    name = "linear_regression"
    description = (
        "Линейная регрессия y ~ x: slope, intercept, r_squared, p_value наклона и его stderr"
    )
    transforms_data = False

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) != 2:
            raise ValueError("linear_regression requires [y_column, x_column]")
        ys, xs = paired_values(db, input_table, columns[0], columns[1])
        fitted: tuple[object, ...] = (None, None, None, None, None)
        if len(xs) >= 2 and np.ptp(np.asarray(xs)) > 0:
            fit = stats.linregress(np.asarray(xs), np.asarray(ys))
            fitted = (fit.slope, fit.intercept, fit.rvalue**2, fit.pvalue, fit.stderr)
        return result(db, input_table, "slope, intercept, r_squared, p_value, stderr", fitted)
