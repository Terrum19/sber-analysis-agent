"""Fuzzy search for values stored in a categorical column."""

from difflib import SequenceMatcher

from app.core.tools.base import BaseTool, identifier
from app.core.tools.stats._common import rows_result


class SearchCategoricalValuesTool(BaseTool):
    name = "search_categorical_values"
    description = (
        "Fuzzy-поиск реальных значений категории; columns=[target_column, query_term, limit?]"
    )
    transforms_data = False

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) not in (2, 3):
            raise ValueError("search_categorical_values requires [column, search_text, limit?]")
        column = identifier(columns[0])
        query = columns[1].strip()
        if not query:
            raise ValueError("search_categorical_values requires a non-empty query")
        limit = int(kwargs.get("limit", columns[2] if len(columns) == 3 else 10))
        if limit < 1:
            raise ValueError("search_categorical_values limit must be positive")
        rows = db.query(
            f"SELECT DISTINCT CAST({column} AS VARCHAR) AS stored_value "
            f"FROM {identifier(input_table)} WHERE {column} IS NOT NULL"
        )
        query_lower = query.casefold()
        scored = [
            (
                row["stored_value"],
                SequenceMatcher(None, query_lower, str(row["stored_value"]).casefold()).ratio(),
            )
            for row in rows
        ]
        matches = [(value, score) for value, score in scored if score > 0.3]
        matches.sort(key=lambda item: (-item[1], str(item[0]).casefold()))
        return rows_result(
            db,
            input_table,
            ["stored_value", "match_score"],
            matches[:limit],
        )
