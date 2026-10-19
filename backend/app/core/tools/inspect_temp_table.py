"""Inspect the schema, sample and numeric summary of an intermediate table."""

import json

from app.core.tools.base import BaseTool, identifier
from app.core.tools.stats._common import result


class InspectTempTableTool(BaseTool):
    name = "inspect_temp_table"
    description = "Показать схему, типы, размер, sample rows и numeric descriptive stats"
    transforms_data = False

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) > 1:
            raise ValueError("inspect_temp_table requires [table_name?]")
        table = identifier(columns[0] if columns else input_table)
        schema_rows = db.query(f"DESCRIBE {table}")
        if not schema_rows:
            raise ValueError(f"Table has no columns: {table}")
        sample_rows = db.to_records(table, limit=5)
        row_count = db.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        descriptive_stats: dict[str, dict[str, object]] = {}
        for column in schema_rows:
            name = column["column_name"]
            data_type = str(column["column_type"]).upper()
            if any(kind in data_type for kind in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL")):
                stats_row = db.query(
                    f"SELECT AVG({identifier(name)}) AS mean, "
                    f"MIN({identifier(name)}) AS min, "
                    f"MAX({identifier(name)}) AS max "
                    f"FROM {table}"
                )[0]
                descriptive_stats[name] = stats_row
        return result(
            db,
            input_table,
            "schema, dtypes, row_count, sample_rows, descriptive_stats",
            (
                json.dumps([row["column_name"] for row in schema_rows]),
                json.dumps([row["column_type"] for row in schema_rows]),
                row_count,
                json.dumps(sample_rows, default=str),
                json.dumps(descriptive_stats, default=str),
            ),
        )
