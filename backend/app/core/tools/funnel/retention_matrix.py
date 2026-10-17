"""User retention matrix by cohort and period."""

from app.core.tools.base import BaseTool, identifier
from app.core.tools.funnel._common import columns


class RetentionMatrixTool(BaseTool):
    name = "retention_matrix"
    description = "Матрица удержания пользователей по когортам и периодам"

    def run(self, db, input_table: str, columns_: list[str]) -> str:
        columns(columns_, 2, 2, self.name)
        user_col, period_col = columns_
        table = identifier(input_table)
        user = identifier(user_col)
        period = identifier(period_col)
        return db.save_step_result(
            input_table,
            f"""
            WITH user_cohorts AS (
                SELECT {user} AS user_id, MIN({period}) AS cohort
                FROM {table}
                GROUP BY {user}
            ),
            cohort_sizes AS (
                SELECT cohort, COUNT(*) AS cohort_users
                FROM user_cohorts GROUP BY cohort
            ),
            activity AS (
                SELECT DISTINCT {user} AS user_id, {period} AS period
                FROM {table}
            )
            SELECT c.cohort, a.period, COUNT(*) AS active_users,
                   s.cohort_users,
                   CAST(COUNT(*) AS FLOAT) / s.cohort_users AS retention_rate
            FROM activity a
            JOIN user_cohorts c ON c.user_id = a.user_id
            JOIN cohort_sizes s ON s.cohort = c.cohort
            GROUP BY c.cohort, a.period, s.cohort_users
            ORDER BY c.cohort, a.period
            """,
        )
