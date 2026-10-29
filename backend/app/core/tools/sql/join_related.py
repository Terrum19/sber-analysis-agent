"""Тул join_related: присоединить связанные таблицы по ключам (#165).

Планировщик называет только нужные таблицы. Путь до них — в том числе через
промежуточные таблицы (заказы -> клиенты -> города -> регионы) — и условия
соединения берутся из связей паспорта (core/data/relations.py), поэтому
цепочку из трёх и более таблиц не нужно расписывать шагами join_tables.
"""

import asyncio
from typing import Any

from pydantic import Field

from app.core.data import relations
from app.core.tools.base import BaseTool, TableRef, ToolArgs, ToolContext, identifier


class JoinRelatedArgs(ToolArgs):
    tables: list[TableRef] = Field(min_length=1)
    keep_unmatched: bool = True


class JoinRelatedTool(BaseTool):
    name = "join_related"
    description = (
        "Присоединить связанные таблицы (tables — их имена) по ключам из паспорта: путь "
        "через промежуточные таблицы и условия соединения найдутся сами; колонка, имя "
        "которой есть в нескольких связанных таблицах, называется <таблица>_<колонка>. "
        "keep_unmatched=false — только строки, у которых нашлась пара"
    )
    Args = JoinRelatedArgs

    async def arun(
        self,
        ctx: ToolContext,
        db,
        input_table: str,
        columns: list[Any] | None = None,
        /,
        **arguments: Any,
    ) -> str:
        """Путь ищется от исходной таблицы хода ($source): связи знают её, а не temp_result."""
        args = self.parse_args(columns, arguments)
        start = ctx.source or input_table
        return await asyncio.to_thread(self.join, db, input_table, start, args)

    def execute(self, db, input_table: str, args: JoinRelatedArgs) -> str:
        return self.join(db, input_table, input_table, args)

    def join(self, db, input_table: str, start: str, args: JoinRelatedArgs) -> str:
        """Одним запросом: input AS t0, затем по LEFT/INNER JOIN на каждый шаг пути.

        Ключи присоединённой таблицы из условия не берутся: они равны ключам
        слева. Ключи исходной таблицы должны остаться во входе шага. Имена
        колонок — relations.output_names; имя, уже занятое во входе (колонка
        прошлого шага), получает номер.
        """
        links = relations.load(db)
        try:
            hops = relations.join_hops(links, start, args.tables)
        except ValueError as error:
            raise ValueError(f"join_related: {error}") from None
        if not hops:
            raise ValueError(
                f"join_related: таблицы {', '.join(args.tables)} уже в данных шага — "
                "соединять нечего"
            )
        present = db.columns(input_table)
        taken = {name.lower() for name in present}
        renamed = relations.output_names(db, links, start)
        aliases = {start: "t0"}
        select = ["t0.*"]
        joins = []
        join = "LEFT JOIN" if args.keep_unmatched else "JOIN"
        for number, hop in enumerate(hops, 1):
            left, alias = aliases[hop.from_table], f"t{number}"
            missing = [name for name in hop.from_columns if left == "t0" and name not in present]
            if missing:
                raise ValueError(
                    f"join_related: во входе шага нет ключа {hop.from_table}."
                    f"{', '.join(missing)}: соединяй до group_by и select_columns"
                )
            condition = " AND ".join(
                f"{left}.{identifier(a)} = {alias}.{identifier(b)}"
                for a, b in zip(hop.from_columns, hop.to_columns, strict=True)
            )
            joins.append(f"{join} {identifier(hop.to_table)} AS {alias} ON {condition}")
            aliases[hop.to_table] = alias
            for column in db.columns(hop.to_table):
                if column in hop.to_columns:
                    continue
                name = _free_name(renamed[hop.to_table][column], taken)
                taken.add(name.lower())
                select.append(f"{alias}.{identifier(column)} AS {identifier(name)}")
        return db.save_step_result(
            input_table,
            f"SELECT {', '.join(select)} FROM {identifier(input_table)} AS t0 {' '.join(joins)}",
        )


def _free_name(name: str, taken: set[str]) -> str:
    """Имя колонки в результате (relations.output_names), занятое — с номером: name_2."""
    free = name
    number = 2
    while free.lower() in taken:
        free = f"{name}_{number}"
        number += 1
    return free
