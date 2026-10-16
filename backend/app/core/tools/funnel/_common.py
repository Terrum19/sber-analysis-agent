"""Shared validation and row helpers for funnel tools."""

from app.core.tools.base import identifier
from app.core.tools.stats._common import rows_result


def columns(columns: list[str], minimum: int, maximum: int, name: str) -> None:
    if not minimum <= len(columns) <= maximum:
        raise ValueError(f"{name} requires between {minimum} and {maximum} columns")
    for column in columns:
        identifier(column)


def ordered_steps(db, table: str, step_col: str, order_col: str) -> list[dict]:
    return db.query(
        f"SELECT {identifier(step_col)} AS step, MIN({identifier(order_col)}) AS first_order "
        f"FROM {identifier(table)} GROUP BY {identifier(step_col)} "
        "ORDER BY first_order, step"
    )


__all__ = ["columns", "identifier", "ordered_steps", "rows_result"]
