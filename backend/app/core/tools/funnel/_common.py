"""Shared argument models and row helpers for funnel tools."""

from app.core.tools.base import Ident, ToolArgs, identifier
from app.core.tools.stats._common import rows_result


class StepOrderArgs(ToolArgs):
    step_column: Ident
    order_column: Ident


def ordered_steps(db, table: str, step_col: str, order_col: str) -> list[dict]:
    return db.query(
        f"SELECT {identifier(step_col)} AS step, MIN({identifier(order_col)}) AS first_order "
        f"FROM {identifier(table)} GROUP BY {identifier(step_col)} "
        "ORDER BY first_order, step"
    )


__all__ = ["StepOrderArgs", "identifier", "ordered_steps", "rows_result"]
