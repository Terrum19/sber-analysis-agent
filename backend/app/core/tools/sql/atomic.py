"""Composable SQL tools used as building blocks for research plans."""

from app.core.tools.base import BaseTool, expression, identifier


class SelectColumnsTool(BaseTool):
    name = "select_columns"
    description = "Выбрать указанные колонки"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if not columns:
            raise ValueError("select_columns requires at least one column")
        selected = ", ".join(identifier(column) for column in columns)
        return db.save_step_result(input_table, f"SELECT {selected} FROM {identifier(input_table)}")


class FilterRowsTool(BaseTool):
    name = "filter_rows"
    description = "Отфильтровать строки по колонке, значению и оператору"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 3:
            raise ValueError("filter_rows requires [column, value, operator]")
        column, value, operator = identifier(columns[0]), columns[1], columns[2].upper()
        if operator not in {"=", ">", "<", ">=", "<=", "!=", "LIKE", "ILIKE"}:
            raise ValueError(f"Unsupported filter operator: {operator}")
        escaped = value.replace("'", "''")
        return db.save_step_result(
            input_table,
            f"SELECT * FROM {identifier(input_table)} WHERE {column} {operator} '{escaped}'",
        )


class CreateColumnTool(BaseTool):
    name = "create_column"
    description = "Создать вычисляемую колонку по SQL-выражению"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("create_column requires [new_column, expression]")
        new_column, sql_expression = identifier(columns[0]), expression(columns[1])
        return db.save_step_result(
            input_table,
            f"SELECT *, ({sql_expression}) AS {new_column} FROM {identifier(input_table)}",
        )


class RenameColumnTool(BaseTool):
    name = "rename_column"
    description = "Переименовать колонку"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("rename_column requires [old_name, new_name]")
        old_name, new_name = identifier(columns[0]), identifier(columns[1])
        return db.save_step_result(
            input_table,
            f"SELECT * EXCLUDE ({old_name}), {old_name} AS {new_name} "
            f"FROM {identifier(input_table)}",
        )


class JoinTablesTool(BaseTool):
    name = "join_tables"
    description = "Соединить текущую таблицу с другой по условию"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("join_tables requires [right_table, join_condition]")
        right_table, condition = identifier(columns[0]), expression(columns[1])
        return db.save_step_result(
            input_table,
            f"SELECT * FROM {identifier(input_table)} AS t "
            f"JOIN {right_table} AS t_right ON {condition}",
        )


class LeftJoinTablesTool(JoinTablesTool):
    name = "left_join_tables"
    description = "Левое соединение с другой таблицей без потери строк"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("left_join_tables requires [right_table, join_condition]")
        right_table, condition = identifier(columns[0]), expression(columns[1])
        return db.save_step_result(
            input_table,
            f"SELECT * FROM {identifier(input_table)} AS t "
            f"LEFT JOIN {right_table} AS t_right ON {condition}",
        )


class GroupByTool(BaseTool):
    name = "group_by"
    description = "Сгруппировать и применить COUNT, SUM, AVG, MIN или MAX"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 3:
            raise ValueError("group_by requires [group_column, aggregate_column, aggregate_function]")
        group_col, agg_col, agg_func = identifier(columns[0]), identifier(columns[1]), columns[2].upper()
        if agg_func not in {"COUNT", "SUM", "AVG", "MIN", "MAX"}:
            raise ValueError(f"Unsupported aggregate function: {agg_func}")
        return db.save_step_result(
            input_table,
            f"SELECT {group_col}, {agg_func}({agg_col}) AS {agg_func.lower()}_{agg_col} "
            f"FROM {identifier(input_table)} GROUP BY {group_col}",
        )


class PivotTool(BaseTool):
    name = "pivot"
    description = "Сделать pivot по уникальным значениям категориальной колонки"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 3:
            raise ValueError("pivot requires [value_column, row_column, column_column]")
        value_col, row_col, col_col = (identifier(column) for column in columns)
        return db.save_step_result(
            input_table,
            f"PIVOT {identifier(input_table)} ON {col_col} USING SUM({value_col}) "
            f"GROUP BY {row_col}",
        )


class TimeBucketTool(BaseTool):
    name = "time_bucket"
    description = "Агрегировать данные по month, week, day или quarter"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError("time_bucket requires [date_column, interval]")
        date_col, interval = identifier(columns[0]), columns[1].lower()
        if interval not in {"month", "week", "day", "quarter"}:
            raise ValueError(f"Unsupported interval: {interval}")
        return db.save_step_result(
            input_table,
            f"SELECT date_trunc('{interval}', {date_col}) AS period, COUNT(*) AS n "
            f"FROM {identifier(input_table)} GROUP BY period ORDER BY period",
        )


class WindowTool(BaseTool):
    def __init__(self, name: str, function: str, description: str) -> None:
        self.name, self.function, self.description = name, function, description

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) != 2:
            raise ValueError(f"{self.name} requires [column, offset]")
        column, offset = identifier(columns[0]), int(columns[1])
        if offset < 0:
            raise ValueError("Window offset must be non-negative")
        return db.save_step_result(
            input_table,
            f"SELECT *, {self.function}({column}, {offset}) OVER () AS {self.name}_{column} "
            f"FROM {identifier(input_table)}",
        )
