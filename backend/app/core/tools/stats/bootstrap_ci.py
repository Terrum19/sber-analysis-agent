"""Bootstrap confidence interval tool."""

import numpy as np

from app.core.tools.base import BaseTool, identifier
from app.core.tools.stats._common import values


class BootstrapCITool(BaseTool):
    name = "bootstrap_ci"
    description = (
        "Непараметрический bootstrap 95% ДИ для mean/median/sum/min/max; "
        "parameters: n_bootstraps, seed"
    )

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
        if len(columns) not in (1, 2, 3):
            raise ValueError("bootstrap_ci requires [column, statistic?, n_bootstraps?]")
        data_count = db.query(
            f"SELECT COUNT(*) AS n FROM {identifier(input_table)} "
            f"WHERE {identifier(columns[0])} IS NOT NULL"
        )[0]["n"]
        max_observations = int(kwargs.get("max_observations", self.max_observations))
        if data_count > max_observations:
            raise ValueError(
                f"bootstrap_ci supports at most {max_observations} observations; "
                "sample the data before bootstrapping"
            )
        data = values(db, input_table, columns[0])
        statistic = columns[1].lower() if len(columns) >= 2 else "mean"
        if statistic not in {"mean", "median", "sum", "min", "max"}:
            raise ValueError("Unsupported bootstrap statistic")
        n_bootstraps = int(kwargs.get("n_bootstraps", columns[2] if len(columns) == 3 else 1000))
        if n_bootstraps < 1:
            raise ValueError("n_bootstraps must be positive")
        max_bootstraps = int(kwargs.get("max_bootstraps", self.max_bootstraps))
        if n_bootstraps > max_bootstraps:
            raise ValueError(f"n_bootstraps cannot exceed {max_bootstraps}")
        if not data:
            raise ValueError("bootstrap_ci requires at least one observation")
        seed = kwargs.get("seed", self.default_seed)
        rng = np.random.default_rng(None if seed is None else int(seed))
        operations = {
            "mean": np.mean,
            "median": np.median,
            "sum": np.sum,
            "min": np.min,
            "max": np.max,
        }
        estimate = float(operations[statistic](data))
        # Process in bounded batches so the product of sample size and
        # bootstrap count cannot allocate an unexpectedly huge matrix.
        bootstrap_values = np.concatenate(
            [
                operations[statistic](
                    rng.choice(
                        np.asarray(data),
                        size=(min(256, n_bootstraps - start), len(data)),
                        replace=True,
                    ),
                    axis=1,
                )
                for start in range(0, n_bootstraps, 256)
            ]
        )
        ci_low, ci_high = np.quantile(bootstrap_values, [0.025, 0.975])
        if statistic == "mean" and len(columns) == 1:
            return db.save_step_result(
                input_table,
                f"SELECT {estimate} AS mean, {ci_low} AS ci_low, {ci_high} AS ci_high",
            )
        return db.save_step_result(
            input_table,
            "SELECT "
            f"'{statistic}' AS statistic, {estimate} AS estimate, "
            f"{ci_low} AS ci_low, {ci_high} AS ci_high",
        )
