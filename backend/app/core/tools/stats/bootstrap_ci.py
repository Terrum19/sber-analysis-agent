"""Bootstrap confidence interval tool."""

from typing import Literal

import numpy as np
from pydantic import Field

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier
from app.core.tools.stats._common import values

MAX_OBSERVATIONS = 100_000
MAX_BOOTSTRAPS = 10_000


class BootstrapCIArgs(ToolArgs):
    column: Ident
    statistic: Literal["mean", "median", "sum", "min", "max"] | None = None
    n_bootstraps: int = Field(default=1000, ge=1, le=MAX_BOOTSTRAPS)
    seed: int | None = 0
    max_observations: int = Field(default=MAX_OBSERVATIONS, ge=1, le=MAX_OBSERVATIONS)


class BootstrapCITool(BaseTool):
    name = "bootstrap_ci"
    description = (
        "Непараметрический bootstrap 95% ДИ для mean/median/sum/min/max; "
        f"не более {MAX_OBSERVATIONS} наблюдений (иначе сначала sample)"
    )
    kind = "analysis"
    Args = BootstrapCIArgs

    def execute(self, db, input_table: str, args: BootstrapCIArgs) -> str:
        data_count = db.query(
            f"SELECT COUNT(*) AS n FROM {identifier(input_table)} WHERE {args.column} IS NOT NULL"
        )[0]["n"]
        if data_count > args.max_observations:
            raise ValueError(
                f"bootstrap_ci supports at most {args.max_observations} observations; "
                "sample the data before bootstrapping"
            )
        data = values(db, input_table, args.column)
        if not data:
            raise ValueError("bootstrap_ci requires at least one observation")
        statistic = args.statistic or "mean"
        rng = np.random.default_rng(args.seed)
        operations = {
            "mean": np.mean,
            "median": np.median,
            "sum": np.sum,
            "min": np.min,
            "max": np.max,
        }
        estimate = float(operations[statistic](data))

        bootstrap_values = np.concatenate(
            [
                operations[statistic](
                    rng.choice(
                        np.asarray(data),
                        size=(min(256, args.n_bootstraps - start), len(data)),
                        replace=True,
                    ),
                    axis=1,
                )
                for start in range(0, args.n_bootstraps, 256)
            ]
        )
        ci_low, ci_high = np.quantile(bootstrap_values, [0.025, 0.975])
        if args.statistic is None:
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
