"""Linear regression tool."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool, Ident, ToolArgs
from app.core.tools.stats._common import paired_values, result


class LinearRegressionArgs(ToolArgs):
    y_column: Ident
    x_column: Ident


class LinearRegressionTool(BaseTool):
    name = "linear_regression"
    description = (
        "Линейная регрессия y ~ x: slope, intercept, r_squared, p_value наклона, его stderr, n"
    )
    kind = "analysis"
    Args = LinearRegressionArgs

    def execute(self, db, input_table: str, args: LinearRegressionArgs) -> str:
        ys, xs = paired_values(db, input_table, args.y_column, args.x_column)
        if len(xs) < 3 or np.ptp(np.asarray(xs)) == 0:
            slope = intercept = r_squared = p_value = stderr = None
        else:
            fit = stats.linregress(np.asarray(xs), np.asarray(ys))
            slope, intercept = float(fit.slope), float(fit.intercept)
            r_squared, p_value = float(fit.rvalue**2), float(fit.pvalue)
            stderr = float(fit.stderr)
        return result(
            db,
            input_table,
            "slope, intercept, r_squared, p_value, stderr, n",
            (slope, intercept, r_squared, p_value, stderr, len(xs)),
        )
