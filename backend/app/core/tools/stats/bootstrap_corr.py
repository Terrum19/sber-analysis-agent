"""Bootstrap confidence interval for Pearson correlation."""

import numpy as np
from pydantic import Field
from scipy import stats

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier
from app.core.tools.stats._common import paired_values, result
from app.core.tools.stats.bootstrap_ci import MAX_BOOTSTRAPS, MAX_OBSERVATIONS


class BootstrapCorrArgs(ToolArgs):
    column_a: Ident
    column_b: Ident
    n_bootstraps: int = Field(default=1000, ge=1, le=MAX_BOOTSTRAPS)
    seed: int | None = 0
    max_observations: int = Field(default=MAX_OBSERVATIONS, ge=1, le=MAX_OBSERVATIONS)


class BootstrapCorrelationTool(BaseTool):
    name = "bootstrap_corr"
    description = "Bootstrap 95% ДИ для корреляции Пирсона"
    kind = "analysis"
    Args = BootstrapCorrArgs

    def execute(self, db, input_table: str, args: BootstrapCorrArgs) -> str:
        data_count = db.query(
            f"SELECT COUNT(*) AS n FROM {identifier(input_table)} "
            f"WHERE {args.column_a} IS NOT NULL AND {args.column_b} IS NOT NULL"
        )[0]["n"]
        if data_count > args.max_observations:
            raise ValueError(
                f"bootstrap_corr supports at most {args.max_observations} observations; "
                "sample the data before bootstrapping"
            )
        first, second = paired_values(db, input_table, args.column_a, args.column_b)
        if len(first) < 2:
            raise ValueError("bootstrap_corr requires paired observations")
        x, y = np.asarray(first), np.asarray(second)
        if np.ptp(x) == 0 or np.ptp(y) == 0:
            correlation = None
            ci_low = ci_high = None
        else:
            rng = np.random.default_rng(args.seed)
            bootstrap = []
            for start in range(0, args.n_bootstraps, 256):
                indices = rng.integers(
                    0,
                    len(x),
                    size=(min(256, args.n_bootstraps - start), len(x)),
                )
                for sample_indices in indices:
                    sampled_x, sampled_y = x[sample_indices], y[sample_indices]
                    if np.ptp(sampled_x) and np.ptp(sampled_y):
                        bootstrap.append(stats.pearsonr(sampled_x, sampled_y).statistic)
            correlation = float(stats.pearsonr(x, y).statistic)
            ci_low, ci_high = (
                (float(value) for value in np.quantile(bootstrap, [0.025, 0.975]))
                if bootstrap
                else (None, None)
            )
        return result(
            db, input_table, "correlation, ci_low, ci_high", (correlation, ci_low, ci_high)
        )
