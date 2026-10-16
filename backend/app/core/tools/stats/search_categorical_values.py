"""Categorical value search tool."""

from app.core.tools.base import BaseTool, identifier


class SearchCategoricalValuesTool(BaseTool):
    name = "search_categorical_values"
    description = "Найти реальные значения категории по подстроке"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("search_categorical_values requires [column, search_text]")
        column, search_text = identifier(columns[0]), columns[1].replace("'", "''")
        return db.save_step_result(
            input_table,
            f"SELECT DISTINCT {column} FROM {identifier(input_table)} "
            f"WHERE CAST({column} AS VARCHAR) ILIKE '%{search_text}%'",
        )
