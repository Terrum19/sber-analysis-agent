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
        # Примеры значений — одним запросом на все колонки, а не SELECT на каждую.
        samples = _sample_values(db, table, [column["column_name"] for column in schema_rows])
        rows = [
            (
                column["column_name"],
                column["column_type"],
                row_count,
                *stats.get(column["column_name"], (None, None, None)),
                samples.get(column["column_name"]),
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


def _sample_values(db, table: str, columns: list[str]) -> dict[str, str | None]:
    """До трёх непустых значений каждой колонки одним запросом (UNION ALL).

    Один SELECT на колонку — это N+1 запросов к DuckDB на широкой таблице.
    Каждая ветка UNION ограничена LIMIT, поэтому константная колонка не
    сканирует всю таблицу ради трёх примеров.
    """
    if not columns:
        return {}
    # table уже прогэкранирован identifier() в execute().
    table_name = table
    selects = [
        f"(SELECT {index} AS column_index, CAST({identifier(name)} AS VARCHAR) AS value "
        f"FROM {table_name} WHERE {identifier(name)} IS NOT NULL LIMIT {SAMPLE_LIMIT})"
        for index, name in enumerate(columns)
    ]
    rows = db.query(
        f"SELECT column_index, value FROM ({' UNION ALL '.join(selects)}) ORDER BY column_index"
    )
    examples: dict[int, list[str]] = {}
    for row in rows:
        examples.setdefault(int(row["column_index"]), []).append(_short(str(row["value"])))
    samples: dict[str, str | None] = {}
    for index, name in enumerate(columns):
        text = ", ".join(examples.get(index, []))
        if not text:
            samples[name] = None
            continue
        samples[name] = (
            text if len(text) <= SAMPLE_VALUES_WIDTH else text[: SAMPLE_VALUES_WIDTH - 1] + "…"
        )
    return samples


def _short(value: str) -> str:
    """Схлопнуть пробелы и обрезать длинное значение примера."""
    text = " ".join(value.split())
    return text if len(text) <= SAMPLE_WIDTH else text[: SAMPLE_WIDTH - 1] + "…"
