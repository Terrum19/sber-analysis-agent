"""Composable SQL tools used as building blocks for research plans."""

import re
from typing import Literal

from pydantic import Field, model_validator

from app.core.tools.base import BaseTool, Expr, Ident, TableRef, ToolArgs, identifier, literal

Scalar = str | int | float | bool

AggFunc = Literal["COUNT", "COUNT_DISTINCT", "SUM", "AVG", "MIN", "MAX", "MEDIAN", "STDDEV"]
Interval = Literal["day", "week", "month", "quarter", "year"]


def _upper(value: object) -> object:
    return value.upper() if isinstance(value, str) else value


def aggregate_sql(function: str, column: str | None) -> str:
    """SQL агрегата; COUNT без колонки — COUNT(*)."""
    if function == "COUNT":
        return f"COUNT({identifier(column)})" if column else "COUNT(*)"
    if column is None:
        raise ValueError(f"{function} requires a column")
    if function == "COUNT_DISTINCT":
        return f"COUNT(DISTINCT {identifier(column)})"
    if function == "STDDEV":
        return f"STDDEV_SAMP({identifier(column)})"
    return f"{function}({identifier(column)})"


def aggregate_alias(function: str, column: str | None) -> str:
    return f"{function.lower()}_{column}" if column else "n"


class SelectColumnsArgs(ToolArgs):
    columns: list[Ident] = Field(min_length=1)


class SelectColumnsTool(BaseTool):
    name = "select_columns"
    description = "Оставить только указанные колонки"
    Args = SelectColumnsArgs

    def execute(self, db, input_table: str, args: SelectColumnsArgs) -> str:
        return db.save_step_result(
            input_table, f"SELECT {', '.join(args.columns)} FROM {identifier(input_table)}"
        )


FilterOperator = Literal[
    "=",
    "!=",
    ">",
    "<",
    ">=",
    "<=",
    "LIKE",
    "ILIKE",
    "IN",
    "NOT IN",
    "BETWEEN",
    "IS NULL",
    "IS NOT NULL",
]


class FilterRowsArgs(ToolArgs):
    column: Ident
    value: Scalar | list[Scalar] | None = None
    operator: FilterOperator = "="

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict) and "operator" in data:
            data["operator"] = " ".join(str(_upper(data["operator"])).split())
        return data

    @model_validator(mode="after")
    def _check_value(self) -> "FilterRowsArgs":
        if self.operator in {"IS NULL", "IS NOT NULL"}:
            return self
        if self.operator in {"IN", "NOT IN"}:
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"{self.operator} requires a non-empty list value")
        elif self.operator == "BETWEEN":
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError("BETWEEN requires value=[low, high]")
        elif self.value is None or isinstance(self.value, list):
            raise ValueError(f"operator {self.operator} requires a single value")
        return self


class FilterRowsTool(BaseTool):
    name = "filter_rows"
    description = (
        "Отфильтровать строки: column operator value. IN/NOT IN — value списком, "
        "BETWEEN — value=[от, до] (подходит для диапазона дат), IS NULL без value. "
        "Несколько условий (AND) — несколько шагов filter_rows подряд"
    )
    Args = FilterRowsArgs

    def execute(self, db, input_table: str, args: FilterRowsArgs) -> str:
        if args.operator in {"IS NULL", "IS NOT NULL"}:
            condition = f"{args.column} {args.operator}"
        elif args.operator in {"IN", "NOT IN"}:
            values = ", ".join(literal(value) for value in args.value)  # type: ignore[union-attr]
            condition = f"{args.column} {args.operator} ({values})"
        elif args.operator == "BETWEEN":
            low, high = args.value  # type: ignore[misc]
            condition = f"{args.column} BETWEEN {literal(low)} AND {literal(high)}"
        else:
            condition = f"{args.column} {args.operator} {literal(args.value)}"
        return db.save_step_result(
            input_table, f"SELECT * FROM {identifier(input_table)} WHERE {condition}"
        )


class CreateColumnArgs(ToolArgs):
    new_column: Ident
    expression: Expr


class CreateColumnTool(BaseTool):
    name = "create_column"
    description = "Создать вычисляемую колонку по SQL-выражению (DuckDB)"
    Args = CreateColumnArgs

    def execute(self, db, input_table: str, args: CreateColumnArgs) -> str:
        return db.save_step_result(
            input_table,
            f"SELECT *, ({args.expression}) AS {args.new_column} FROM {identifier(input_table)}",
        )


class RenameColumnArgs(ToolArgs):
    old_name: Ident
    new_name: Ident


class RenameColumnTool(BaseTool):
    name = "rename_column"
    description = "Переименовать колонку"
    Args = RenameColumnArgs

    def execute(self, db, input_table: str, args: RenameColumnArgs) -> str:
        return db.save_step_result(
            input_table,
            f"SELECT * EXCLUDE ({args.old_name}), {args.old_name} AS {args.new_name} "
            f"FROM {identifier(input_table)}",
        )


class JoinArgs(ToolArgs):
    right_table: TableRef
    condition: Expr


class JoinTablesTool(BaseTool):
    name = "join_tables"
    description = (
        "Соединить текущую таблицу с другой (right_table — имя источника или $step_N); "
        "в condition используйте t и t_right, например t.id = t_right.id"
    )
    Args = JoinArgs
    join = "JOIN"

    def execute(self, db, input_table: str, args: JoinArgs) -> str:
        condition = re.sub(r"\bt1\.", "t.", args.condition)
        condition = re.sub(r"\bt2\.", "t_right.", condition)
        return db.save_step_result(
            input_table,
            f"SELECT * FROM {identifier(input_table)} AS t "
            f"{self.join} {args.right_table} AS t_right ON {condition}",
        )


class LeftJoinTablesTool(JoinTablesTool):
    name = "left_join_tables"
    description = "Как join_tables, но LEFT JOIN — без потери строк текущей таблицы"
    join = "LEFT JOIN"


class Metric(ToolArgs):
    function: AggFunc
    column: Ident | None = None
    alias: Ident | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict) and "function" in data:
            data["function"] = _upper(data["function"])
        return data


class GroupByArgs(ToolArgs):
    by: Ident | list[Ident]
    column: Ident | None = None
    function: AggFunc = "COUNT"
    metrics: list[Metric] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict) and "function" in data:
            data["function"] = _upper(data["function"])
        return data


class GroupByTool(BaseTool):
    name = "group_by"
    description = (
        "Сгруппировать по одной или нескольким колонкам (by) и посчитать агрегат "
        "function(column); несколько агрегатов — через metrics=[{function, column, alias?}]. "
        "Функции: COUNT, COUNT_DISTINCT, SUM, AVG, MIN, MAX, MEDIAN, STDDEV"
    )
    Args = GroupByArgs

    def execute(self, db, input_table: str, args: GroupByArgs) -> str:
        by = [args.by] if isinstance(args.by, str) else args.by
        if not by:
            raise ValueError("group_by requires at least one grouping column")
        metrics = args.metrics or [Metric(function=args.function, column=args.column)]
        selected = [
            f"{aggregate_sql(m.function, m.column)} AS "
            f"{m.alias or aggregate_alias(m.function, m.column)}"
            for m in metrics
        ]
        keys = ", ".join(by)
        return db.save_step_result(
            input_table,
            f"SELECT {keys}, {', '.join(selected)} FROM {identifier(input_table)} "
            f"GROUP BY {keys} ORDER BY {keys}",
        )


class PivotArgs(ToolArgs):
    value_column: Ident
    row_column: Ident
    column_column: Ident
    function: Literal["SUM", "AVG", "COUNT", "MIN", "MAX"] = "SUM"


class PivotTool(BaseTool):
    name = "pivot"
    description = "Pivot: строки по row_column, колонки по значениям column_column"
    Args = PivotArgs

    def execute(self, db, input_table: str, args: PivotArgs) -> str:
        return db.save_step_result(
            input_table,
            f"PIVOT {identifier(input_table)} ON {args.column_column} "
            f"USING {args.function}({args.value_column}) GROUP BY {args.row_column}",
        )


class TimeBucketArgs(ToolArgs):
    date_column: Ident
    interval: Interval = "month"
    metric_column: Ident | None = None
    function: AggFunc = "COUNT"
    by: list[Ident] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict):
            if isinstance(data.get("interval"), str):
                data["interval"] = data["interval"].lower()
            if "function" in data:
                data["function"] = _upper(data["function"])
            if isinstance(data.get("by"), str):
                data["by"] = [data["by"]]
        return data


class TimeBucketTool(BaseTool):
    name = "time_bucket"
    description = (
        "Агрегировать по периодам (day/week/month/quarter/year): function(metric_column) "
        "в колонке period; без metric_column — число строк n. by — доп. разрезы"
    )
    Args = TimeBucketArgs

    def execute(self, db, input_table: str, args: TimeBucketArgs) -> str:
        if args.metric_column is None and args.function != "COUNT":
            raise ValueError(f"time_bucket: {args.function} requires metric_column")
        metric = (
            f"{aggregate_sql(args.function, args.metric_column)} AS "
            f"{aggregate_alias(args.function, args.metric_column)}"
        )
        keys = ", ".join(["period", *args.by])
        extra = "".join(f", {column}" for column in args.by)
        return db.save_step_result(
            input_table,
            f"SELECT date_trunc('{args.interval}', {args.date_column}) AS period{extra}, "
            f"{metric} FROM {identifier(input_table)} GROUP BY {keys} ORDER BY {keys}",
        )


class SortLimitArgs(ToolArgs):
    order_by: Ident | list[Ident]
    descending: bool = True
    limit: int | None = Field(default=None, ge=1, le=100_000)


class SortLimitTool(BaseTool):
    name = "sort_limit"
    description = "Отсортировать по колонкам и взять первые limit строк (топ-N)"
    Args = SortLimitArgs

    def execute(self, db, input_table: str, args: SortLimitArgs) -> str:
        order = [args.order_by] if isinstance(args.order_by, str) else args.order_by
        direction = "DESC" if args.descending else "ASC"
        limit = f" LIMIT {args.limit}" if args.limit else ""
        return db.save_step_result(
            input_table,
            f"SELECT * FROM {identifier(input_table)} "
            f"ORDER BY {', '.join(f'{column} {direction}' for column in order)}{limit}",
        )


class SampleArgs(ToolArgs):
    n: int | None = Field(default=None, ge=1, le=1_000_000)
    fraction: float | None = Field(default=None, gt=0, le=1)
    seed: int = 0

    @model_validator(mode="after")
    def _one_of(self) -> "SampleArgs":
        if (self.n is None) == (self.fraction is None):
            raise ValueError("sample requires exactly one of n or fraction")
        return self


class SampleTool(BaseTool):
    name = "sample"
    description = "Случайная выборка строк (n строк или доля fraction), воспроизводимая по seed"
    Args = SampleArgs

    def execute(self, db, input_table: str, args: SampleArgs) -> str:
        size = f"{args.n} ROWS" if args.n else f"{args.fraction * 100}%"
        return db.save_step_result(
            input_table,
            f"SELECT * FROM {identifier(input_table)} "
            f"USING SAMPLE reservoir({size}) REPEATABLE ({args.seed})",
        )


class WindowArgs(ToolArgs):
    column: Ident
    offset: int = Field(default=1, ge=0)
    order_column: Ident | None = None
    partition_by: list[Ident] = Field(default_factory=list)


def _over(order_column: str | None, partition_by: list[str], frame: str = "") -> str:
    partition = f"PARTITION BY {', '.join(partition_by)} " if partition_by else ""
    return f"OVER ({partition}ORDER BY {order_column or 'rowid'}{frame})"


class WindowTool(BaseTool):
    Args = WindowArgs

    def __init__(self, name: str, function: str, description: str) -> None:
        self.name, self.function = name, function
        self.description = f"{description}; partition_by — внутри групп"

    def execute(self, db, input_table: str, args: WindowArgs) -> str:
        return db.save_step_result(
            input_table,
            f"SELECT *, {self.function}({args.column}, {args.offset}) "
            f"{_over(args.order_column, args.partition_by)} AS {self.name}_{args.column} "
            f"FROM {identifier(input_table)}",
        )


WindowFunction = Literal[
    "cumulative_sum",
    "rolling_avg",
    "pct_change",
    "rank",
    "dense_rank",
    "row_number",
    "share_of_total",
]


class WindowMetricArgs(ToolArgs):
    function: WindowFunction
    column: Ident
    order_column: Ident | None = None
    partition_by: list[Ident] = Field(default_factory=list)
    window: int = Field(default=3, ge=1, le=1000)


class WindowMetricTool(BaseTool):
    name = "window_metric"
    description = (
        "Оконная метрика как новая колонка {function}_{column}: cumulative_sum, "
        "rolling_avg (окно window строк), pct_change (к предыдущей строке), "
        "rank/dense_rank/row_number (по убыванию column), share_of_total (доля от суммы); "
        "partition_by — считать внутри групп"
    )
    Args = WindowMetricArgs

    def execute(self, db, input_table: str, args: WindowMetricArgs) -> str:
        col, part = args.column, args.partition_by
        partition = f"PARTITION BY {', '.join(part)}" if part else ""
        expressions = {
            "cumulative_sum": f"SUM({col}) {_over(args.order_column, part)}",
            "rolling_avg": f"AVG({col}) "
            + _over(
                args.order_column,
                part,
                f" ROWS BETWEEN {args.window - 1} PRECEDING AND CURRENT ROW",
            ),
            "pct_change": f"({col} - LAG({col}) {_over(args.order_column, part)}) "
            f"/ NULLIF(LAG({col}) {_over(args.order_column, part)}, 0)",
            "rank": f"RANK() OVER ({partition} ORDER BY {col} DESC)",
            "dense_rank": f"DENSE_RANK() OVER ({partition} ORDER BY {col} DESC)",
            "row_number": f"ROW_NUMBER() OVER ({partition} ORDER BY {col} DESC)",
            "share_of_total": f"{col} / NULLIF(SUM({col}) OVER ({partition}), 0)",
        }
        return db.save_step_result(
            input_table,
            f"SELECT *, {expressions[args.function]} AS {args.function}_{col} "
            f"FROM {identifier(input_table)}",
        )
