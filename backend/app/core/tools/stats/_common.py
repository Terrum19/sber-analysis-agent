"""Shared helpers for statistical tools."""

from datetime import date, datetime

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


def result(db, source: str, columns: str, values_: tuple[object, ...]) -> str:
    names = [name.strip() for name in columns.split(",")]
    if len(names) != len(values_):
        raise ValueError("Result column/value count mismatch")
    expressions = []
    for name, value in zip(names, values_):
        literal = (
            f"'{value.replace(chr(39), chr(39) * 2)}'"
            if isinstance(value, str)
            else "NULL"
            if value is None
            else str(value)
        )
        expressions.append(f"{literal} AS {identifier(name)}")
    return db.save_step_result(source, f"SELECT {', '.join(expressions)}")


def rows_result(db, source: str, columns: list[str], rows: list[tuple[object, ...]]) -> str:
    """Materialize a small Python-computed table as a cached step result."""
    if not rows:
        raise ValueError("Tool produced no rows")
    if any(len(row) != len(columns) for row in rows):
        raise ValueError("Result row/column count mismatch")

    def literal(value: object) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (date, datetime)):
            escaped = str(value).replace("'", "''")
            return f"'{escaped}'"
        if isinstance(value, str):
            return f"'{value.replace(chr(39), chr(39) * 2)}'"
        return str(value)

    values_sql = ", ".join(f"({', '.join(literal(value) for value in row)})" for row in rows)
    names = ", ".join(identifier(name) for name in columns)
    return db.save_step_result(source, f"SELECT * FROM (VALUES {values_sql}) AS t({names})")
