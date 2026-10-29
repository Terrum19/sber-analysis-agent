"""Inspect the schema, sample and numeric summary of an intermediate table."""

from app.core.tools.base import BaseTool, TableRef, ToolArgs, identifier
from app.core.tools.stats._common import NUMERIC_TYPES, rows_result

# Паспорт — обычная таблица: строка на колонку, в ячейках только скаляры.
# JSON-массивы в ячейках фронт показывал как мусорную таблицу.
COLUMNS = ["column_name", "dtype", "row_count", "mean", "min", "max", "sample_values"]
# Сколько примеров значений показывать и какой длины: ячейка должна читаться.
SAMPLE_LIMIT = 3
SAMPLE_WIDTH = 40
SAMPLE_VALUES_WIDTH = 120


class InspectTableArgs(ToolArgs):
    table: TableRef | None = None


class InspectTempTableTool(BaseTool):
    name = "inspect_temp_table"
    description = (
        "Показать схему, типы, размер, примеры значений и числовые сводки по колонкам "
        "таблицы (по умолчанию — входной; можно указать $step_N). После шага план будет "
        "достроен"
    )
    kind = "lookup"
    Args = InspectTableArgs

    def execute(self, db, input_table: str, args: InspectTableArgs) -> str:
        table = identifier(args.table or input_table)
        schema_rows = db.query(f"DESCRIBE {table}")
        if not schema_rows:
            raise ValueError(f"Table has no columns: {table}")
        row_count = db.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        stats = _numeric_stats(db, table, schema_rows)
        rows = [
            (
                column["column_name"],
                column["column_type"],
                row_count,
                *stats.get(column["column_name"], (None, None, None)),
                _sample_values(db, table, column["column_name"]),
            )
            for column in schema_rows
        ]
        return rows_result(db, input_table, COLUMNS, rows)


def _numeric_stats(
    db, table: str, schema_rows: list[dict]
) -> dict[str, tuple[object, object, object]]:
    """mean/min/max числовых колонок одним проходом по таблице."""
    numeric = [
        row["column_name"]
        for row in schema_rows
        if str(row["column_type"]).upper().startswith(NUMERIC_TYPES)
    ]
    if not numeric:
        return {}
    parts: list[str] = []
    for index, name in enumerate(numeric):
        column = identifier(name)
        # DOUBLE, а не DECIMAL: в таблице результата значения остаются числами.
        parts += [
            f"CAST(AVG({column}) AS DOUBLE) AS s{index}_mean",
            f"CAST(MIN({column}) AS DOUBLE) AS s{index}_min",
            f"CAST(MAX({column}) AS DOUBLE) AS s{index}_max",
        ]
    row = db.query(f"SELECT {', '.join(parts)} FROM {table}")[0]
    return {
        name: (row[f"s{index}_mean"], row[f"s{index}_min"], row[f"s{index}_max"])
        for index, name in enumerate(numeric)
    }


def _sample_values(db, table: str, column: str) -> str | None:
    """До трёх непустых значений колонки одной строкой, без массивов в ячейке.

    Без DISTINCT: примеры читаются до LIMIT даже у константной колонки,
    а не сканируют всю таблицу ради трёх разных значений.
    """
    rows = db.query(
        f"SELECT CAST({identifier(column)} AS VARCHAR) AS value "
        f"FROM {table} WHERE {identifier(column)} IS NOT NULL "
        f"LIMIT {SAMPLE_LIMIT}"
    )
    examples = [_short(str(row["value"])) for row in rows]
    if not examples:
        return None
    text = ", ".join(examples)
    return text if len(text) <= SAMPLE_VALUES_WIDTH else text[: SAMPLE_VALUES_WIDTH - 1] + "…"


def _short(value: str) -> str:
    """Схлопнуть пробелы и обрезать длинное значение примера."""
    text = " ".join(value.split())
    return text if len(text) <= SAMPLE_WIDTH else text[: SAMPLE_WIDTH - 1] + "…"
