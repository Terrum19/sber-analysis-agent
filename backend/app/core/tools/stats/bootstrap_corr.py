"""Bootstrap confidence interval for Pearson correlation."""

import numpy as np
from scipy import stats

from app.core.tools.base import BaseTool, identifier
from app.core.tools.stats._common import paired_values, result


class BootstrapCorrelationTool(BaseTool):
    name = "bootstrap_corr"
    description = "Bootstrap 95% ДИ для корреляции Пирсона"
    transforms_data = False

    max_observations = 100_000
    max_bootstraps = 10_000
    default_seed = 0

    def run(
        self,
        db,
        input_table: str,
        columns: list[str],
        **kwargs: object,
    ) -> str:
        if len(columns) not in (2, 3):
            raise ValueError("bootstrap_corr requires [column_a, column_b, n_bootstraps?]")
        max_observations = int(kwargs.get("max_observations", self.max_observations))
        data_count = db.query(
            f"SELECT COUNT(*) AS n FROM {identifier(input_table)} "
            f"WHERE {identifier(columns[0])} IS NOT NULL "
            f"AND {identifier(columns[1])} IS NOT NULL"
        )[0]["n"]
        if data_count > max_observations:
            raise ValueError(
                f"bootstrap_corr supports at most {max_observations} observations; "
                "sample the data before bootstrapping"
            )
        first, second = paired_values(db, input_table, columns[0], columns[1])
        n_bootstraps = int(kwargs.get("n_bootstraps", columns[2] if len(columns) == 3 else 1000))
        if n_bootstraps < 1:
            raise ValueError("n_bootstraps must be positive")
        max_bootstraps = int(kwargs.get("max_bootstraps", self.max_bootstraps))
        if n_bootstraps > max_bootstraps:
            raise ValueError(f"n_bootstraps cannot exceed {max_bootstraps}")
        if len(first) != len(second) or len(first) < 2:
            raise ValueError("bootstrap_corr requires paired observations")
        x, y = np.asarray(first), np.asarray(second)
        if np.ptp(x) == 0 or np.ptp(y) == 0:
            correlation = None
            ci_low = ci_high = None
        else:
            seed = kwargs.get("seed", self.default_seed)
            rng = np.random.default_rng(None if seed is None else int(seed))
            bootstrap = []
            for start in range(0, n_bootstraps, 256):
                indices = rng.integers(
                    0,
                    len(x),
                    size=(min(256, n_bootstraps - start), len(x)),
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
