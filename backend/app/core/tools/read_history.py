"""Тул read_history: прошлые ходы сессии старше окна контекста (#197).

Последние ``settings.context_turns`` ходов планировщик видит всегда;
остальная история лежит в SQLite и в промпт не кладётся. Этот lookup-шаг
выдаёт ход по номеру или ищет ходы по вопросам и ответам, после него план
достраивается по найденному. Ходы берутся из памяти сессии, которую
исполнитель передаёт в ToolContext.history.
"""

import json
from typing import Any

from pydantic import model_validator

from app.core.tools.base import BaseTool, ToolArgs, ToolContext
from app.core.tools.stats._common import rows_result

COLUMNS = ["turn", "question", "answer", "status", "plan"]
STATUSES = {"ok": "ответ получен", "failed": "ход упал", "clarification": "ход ждал уточнения"}
NOT_FOUND = "в истории сессии такого хода нет"


class ReadHistoryArgs(ToolArgs):
    query: str | None = None
    turn: int | None = None

    @model_validator(mode="after")
    def _query_or_turn(self) -> "ReadHistoryArgs":
        if not (self.query or "").strip() and self.turn is None:
            raise ValueError("нужен query (слова для поиска) или turn (номер хода)")
        return self


class ReadHistoryTool(BaseTool):
    name = "read_history"
    description = (
        "Прошлые ходы этой сессии старше показанных: ход по номеру (turn) или поиск "
        "по словам вопроса и ответа (query). Дорогой шаг: только если ответа нет "
        "в прошлых ходах и воспоминаниях выше. После шага план будет достроен"
    )
    kind = "lookup"
    Args = ReadHistoryArgs

    async def arun(
        self,
        ctx: ToolContext,
        db,
        input_table: str,
        columns: list[Any] | None = None,
        /,
        **arguments: Any,
    ) -> str:
        args = self.parse_args(columns, arguments)
        if ctx.history is None:
            raise ValueError("read_history: история сессии недоступна")
        turns = ctx.history.find_turns(args.query, args.turn)
        rows = [
            (
                turn["number"],
                turn["question"],
                turn.get("headline") or "",
                STATUSES.get(turn["status"], turn["status"]),
                json.dumps(_plan(turn), ensure_ascii=False, default=str) if turn["steps"] else "",
            )
            for turn in turns
        ]
        return rows_result(
            db, input_table, COLUMNS, rows or [(None, args.query, NOT_FOUND, "", "")]
        )

    def execute(self, db, input_table: str, args: ReadHistoryArgs) -> str:
        raise TypeError("read_history исполняется только через arun: ему нужна память сессии")


def _plan(turn: dict) -> list[dict]:
    return [
        {"tool": step["tool"], "arguments": step["arguments"], "purpose": step["purpose"]}
        for step in turn["steps"]
    ]
