"""Bootstrap confidence interval tool."""

import numpy as np

from app.core.tools.base import BaseTool, identifier


class BootstrapCITool(BaseTool):
    name = "bootstrap_ci"
    description = "Бутстрап 95% доверительного интервала для среднего"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 1:
            raise ValueError("bootstrap_ci requires [column]")
        rows = db.query(f"SELECT {identifier(columns[0])} AS value FROM {identifier(input_table)}")
        values = [float(row["value"]) for row in rows if row["value"] is not None]
        if not values:
            raise ValueError("bootstrap_ci requires at least one observation")
        rng = np.random.default_rng(0)
        samples = np.mean(
            rng.choice(np.asarray(values), size=(1000, len(values)), replace=True),
            axis=1,
        )
        ci_low, ci_high = np.quantile(samples, [0.025, 0.975])
        return db.save_step_result(
            input_table,
            f"SELECT {np.mean(values)} AS mean, {ci_low} AS ci_low, {ci_high} AS ci_high",
        )
