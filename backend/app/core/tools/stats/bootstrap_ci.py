"""Bootstrap confidence interval tool."""

import random
import statistics

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
        rng = random.Random(0)
        samples = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(1000))
        return db.save_step_result(
            input_table,
            f"SELECT {statistics.fmean(values)} AS mean, "
            f"{samples[25]} AS ci_low, {samples[974]} AS ci_high",
        )
