"""Fuzzy search for values stored in a categorical column."""

from difflib import SequenceMatcher

from pydantic import Field, field_validator

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier
from app.core.tools.stats._common import rows_result


def _score(query: str, stored: object) -> float:
    """Похожесть; вхождение запроса подстрокой считается сильным совпадением."""
    text = str(stored).casefold()
    ratio = SequenceMatcher(None, query, text).ratio()
    return max(ratio, 0.9) if query in text else ratio


class SearchCategoricalArgs(ToolArgs):
    column: Ident
    query: str
    limit: int = Field(default=10, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must be non-empty")
        return value.strip()


class SearchCategoricalValuesTool(BaseTool):
    name = "search_categorical_values"
    description = (
        "Найти, как значение реально записано в категориальной колонке (fuzzy-поиск). "
        "Используйте перед filter_rows, если точное написание неизвестно; "
        "после этого шага план будет достроен с учётом найденных значений"
    )
    kind = "lookup"
    Args = SearchCategoricalArgs

    def execute(self, db, input_table: str, args: SearchCategoricalArgs) -> str:
        rows = db.query(
            f"SELECT DISTINCT CAST({identifier(args.column)} AS VARCHAR) AS stored_value "
            f"FROM {identifier(input_table)} WHERE {identifier(args.column)} IS NOT NULL"
        )
        query_lower = args.query.casefold()
        scored = [(row["stored_value"], _score(query_lower, row["stored_value"])) for row in rows]
        matches = [(value, score) for value, score in scored if score > 0.3]
        if not matches:
            raise ValueError(f"No values similar to {args.query!r} in column {args.column}")
        matches.sort(key=lambda item: (-item[1], str(item[0]).casefold()))
        return rows_result(
            db,
            input_table,
            ["stored_value", "match_score"],
            matches[: args.limit],
        )
