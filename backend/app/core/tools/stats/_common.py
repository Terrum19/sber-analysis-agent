"""Shared helpers for statistical tools."""

import math
import numbers
from datetime import date, datetime
from decimal import Decimal

import numpy as np

from app.core.tools.base import identifier


def values(db, table: str, column: str) -> list[float]:
    rows = db.query(f"SELECT {identifier(column)} AS value FROM {identifier(table)}")
    return [float(row["value"]) for row in rows if row["value"] is not None]


def paired_values(
    db, table: str, first_column: str, second_column: str
) -> tuple[list[float], list[float]]:
    """Read numeric pairs from the same rows, excluding incomplete pairs."""
    rows = db.query(
        f"SELECT {identifier(first_column)} AS first_value, "
        f"{identifier(second_column)} AS second_value "
        f"FROM {identifier(table)} "
        f"WHERE {identifier(first_column)} IS NOT NULL "
        f"AND {identifier(second_column)} IS NOT NULL"
    )
    return (
        [float(row["first_value"]) for row in rows],
        [float(row["second_value"]) for row in rows],
    )


def text_literal(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def literal(value: object) -> str:
    """Render a Python value as a typed DuckDB literal.

    Floats become DOUBLE, including NaN and infinities, so results of
    statistical tests are stored as DOUBLE rather than DECIMAL.
    """
    if value is None:
        return "NULL"
    if isinstance(value, (bool, np.bool_)):
        return "TRUE" if value else "FALSE"
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)
    if isinstance(value, (numbers.Real, Decimal)):
        number = float(value)
        if math.isnan(number):
            return "CAST('NaN' AS DOUBLE)"
        if math.isinf(number):
            return "CAST('Infinity' AS DOUBLE)" if number > 0 else "CAST('-Infinity' AS DOUBLE)"
        return f"CAST({number!r} AS DOUBLE)"
    if isinstance(value, datetime):
        kind = "TIMESTAMPTZ" if value.tzinfo is not None else "TIMESTAMP"
        return f"CAST({text_literal(value.isoformat(sep=' '))} AS {kind})"
    if isinstance(value, date):
        return f"CAST({text_literal(value.isoformat())} AS DATE)"
    return text_literal(str(value))


def rows_result(db, source: str, columns: list[str], rows: list[tuple[object, ...]]) -> str:
    """Materialize a small Python-computed table as a cached step result."""
    if not rows:
        raise ValueError("Tool produced no rows")
    if any(len(row) != len(columns) for row in rows):
        raise ValueError("Result row/column count mismatch")
    values_sql = ", ".join(f"({', '.join(literal(value) for value in row)})" for row in rows)
    names = ", ".join(identifier(name) for name in columns)
    return db.save_step_result(source, f"SELECT * FROM (VALUES {values_sql}) AS t({names})")


def result(db, source: str, columns: str, values_: tuple[object, ...]) -> str:
    """Materialize a single result row; columns is a comma-separated list of names."""
    names = [name.strip() for name in columns.split(",")]
    if len(names) != len(values_):
        raise ValueError("Result column/value count mismatch")
    return rows_result(db, source, names, [values_])


NUMERIC_TYPES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
)


def detect_mode(db, table: str, column: str) -> str:
    """Group mode for a text column or a numeric column with exactly two values."""
    row = db.query(
        f"SELECT COUNT(DISTINCT {identifier(column)}) AS levels, "
        f"any_value(typeof({identifier(column)})) AS kind "
        f"FROM {identifier(table)} WHERE {identifier(column)} IS NOT NULL"
    )[0]
    numeric = str(row["kind"] or "").upper().startswith(NUMERIC_TYPES)
    return "samples" if numeric and row["levels"] != 2 else "groups"


def two_samples(
    db, table: str, first: str, second: str, mode: str = "auto"
) -> tuple[list[float], list[float]]:
    """Две выборки: samples — две числовые колонки [value_a, value_b];
    groups — [value_column, group_column] ровно с двумя группами."""
    if mode == "auto":
        mode = detect_mode(db, table, second)
    if mode == "samples":
        return values(db, table, first), values(db, table, second)
    rows = db.query(
        f"SELECT {identifier(first)} AS value, {identifier(second)} AS group_name "
        f"FROM {identifier(table)} "
        f"WHERE {identifier(first)} IS NOT NULL AND {identifier(second)} IS NOT NULL "
        f"ORDER BY group_name"
    )
    groups: dict[object, list[float]] = {}
    for row in rows:
        groups.setdefault(row["group_name"], []).append(float(row["value"]))
    if len(groups) != 2:
        raise ValueError(
            f"Group comparison requires exactly two groups, got {len(groups)}; "
            "use anova_one_way for more"
        )
    first_group, second_group = groups.values()
    return first_group, second_group
