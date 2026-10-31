"""SQL-тул: частоты значений колонки (value counts).

Пример сценария: «Какие значения встречаются чаще всего?».
Для агрегатов по группам — group_by.
"""

from pydantic import Field

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier


class AggregateArgs(ToolArgs):
    column: Ident
    limit: int | None = Field(default=None, ge=1, le=100_000)


class AggregateTool(BaseTool):
    """Сколько раз встречается каждое значение колонки."""

    name = "aggregate"
    description = "Частоты значений колонки (cnt), по убыванию; limit — топ-N"
    Args = AggregateArgs

    def execute(self, db, input_table: str, args: AggregateArgs) -> str:
        limit = f" LIMIT {args.limit}" if args.limit else ""
        column = identifier(args.column)
        sql = (
            f"SELECT {column}, COUNT(*) AS cnt FROM {identifier(input_table)} "
            f"GROUP BY {column} ORDER BY cnt DESC{limit}"
        )
        return db.save_step_result(input_table, sql)
