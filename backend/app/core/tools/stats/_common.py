"""Shared helpers for statistical tools."""

from app.core.tools.base import identifier


def values(db, table: str, column: str) -> list[float]:
    rows = db.query(f"SELECT {identifier(column)} AS value FROM {identifier(table)}")
    return [float(row["value"]) for row in rows if row["value"] is not None]


def result(db, source: str, columns: str, values_: tuple[object, ...]) -> str:
    names = [name.strip() for name in columns.split(",")]
    if len(names) != len(values_):
        raise ValueError("Result column/value count mismatch")
    expressions = []
    for name, value in zip(names, values_):
        literal = (
            f"'{value.replace(chr(39), chr(39) * 2)}'" if isinstance(value, str)
            else "NULL" if value is None
            else str(value)
        )
        expressions.append(f"{literal} AS {identifier(name)}")
    return db.save_step_result(source, f"SELECT {', '.join(expressions)}")
