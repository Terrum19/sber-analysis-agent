"""Ответ на вопрос о прошлых ответах агента («как ты это посчитал?»).

Планировщик помечает такой вопрос маршрутом ``answer`` (ResearchPlan.route):
шагов нет, ответ строится по журналу ходов сессии — вопрос, шаги с
аргументами, сколько строк осталось после каждого шага, ответ и начало
результата. Новых расчётов нет, поэтому числа ответа проверяются по журналу
(как в инсайте, H-Q3): число без опоры — один переспрос.
"""

import json
import logging

import duckdb

from app.core.agent.planner import NO_ANSWER
from app.core.agent.quality_fixes import format_rows, ungrounded_numbers
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.prompts.explainer import EXPLAINER, EXPLAINER_MEMORIES, NUMBER_FIX
from app.core.memory.session_memory import SessionMemory
from app.core.tools.base import identifier
from app.core.tools.registry import tool_registry
from app.models.insight import Insight

logger = logging.getLogger(__name__)

# Сколько строк результата хода видит модель (только у хода, о котором спрашивают).
PREVIEW_ROWS = 10
EMPTY_JOURNAL = "пусто: в этой сессии ещё ничего не считалось"


class Explainer:
    """Пояснение прошлых ответов по журналу ходов, без шагов и SQL."""

    def __init__(self, llm: LLMClient | None = None, db: DuckDBManager | None = None) -> None:
        self.llm = llm or LLMClient()
        self.db = db

    async def answer(self, question: str, memory: SessionMemory) -> Insight:
        journal = describe_journal(memory.explained_turns(), self.db)
        memories = memory.context_memories()
        notes = (
            EXPLAINER_MEMORIES.format(memories="\n".join(f"- {text}" for text in memories))
            if memories
            else ""
        )
        tools = "\n".join(
            f"- {tool.name}: {tool.description}" for tool in tool_registry.planner_tools()
        )
        prompt = EXPLAINER.format(journal=journal, memories=notes, tools=tools, question=question)
        logger.debug("    промпт пояснения=%r", prompt)
        reply = await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
        missing = ungrounded_numbers(_text(reply), prompt)
        if not missing:
            return reply
        logger.warning("пояснение: числа без опоры в журнале %s, переспрос", missing)
        fix = NUMBER_FIX.format(numbers=", ".join(missing))
        return await self.llm.complete_structured("Ты — аналитик.", prompt + fix, Insight)


def _text(reply: Insight) -> str:
    return "\n".join([reply.headline, *reply.details])


def describe_journal(turns: list[dict], db: DuckDBManager | None) -> str:
    """Ходы для пояснения: вопрос, таблица, шаги со строками после них, ответ.

    Начало результата — только у хода, о котором спрашивают: у хода из
    reply_to (``focus``), иначе у последнего хода с результатом.
    """
    if not turns:
        return EMPTY_JOURNAL
    shown = next((turn for turn in turns if turn.get("focus")), None) or next(
        (turn for turn in reversed(turns) if turn.get("result_table")), None
    )
    return "\n".join(_describe_turn(turn, db, preview=turn is shown) for turn in turns)


def _describe_turn(turn: dict, db: DuckDBManager | None, preview: bool) -> str:
    mark = " (пользователь отвечает на этот ход)" if turn.get("focus") else ""
    lines = [f"Ход {turn.get('number', '?')}{mark}. Вопрос: {turn['question']}"]
    tables = turn.get("tables") or []
    if tables:
        rows = _rows(db, tables[0])
        size = f" ({rows} строк)" if rows is not None else ""
        lines.append(f"   Таблица: {tables[0]}{size}" + "".join(f", {t}" for t in tables[1:]))
    results = turn.get("results") or []
    for number, step in enumerate(turn.get("steps") or [], 1):
        lines.append("   " + _describe_step(number, step, results, db))
    if turn.get("headline"):
        lines.append(f"   Ответ: {turn['headline']}")
        lines += [f"     - {detail}" for detail in turn.get("details") or []]
    elif turn.get("status") in NO_ANSWER:
        lines.append(f"   Ответ: {NO_ANSWER[turn['status']]}")
    if preview and turn.get("result_table"):
        lines.append(_preview(db, turn["result_table"]))
    return "\n".join(lines)


def _describe_step(number: int, step: dict, results: list[str], db: DuckDBManager | None) -> str:
    try:
        kind = tool_registry.get(step["tool"]).kind
    except KeyError:
        kind = "?"
    arguments = step.get("arguments") or step.get("columns") or {}
    text = (
        f"Шаг {number}. {step.get('purpose') or step['tool']} — {step['tool']} [{kind}] "
        f"{json.dumps(arguments, ensure_ascii=False, default=str)}, вход {step.get('input')}"
    )
    # results — таблицы шагов по порядку: record_step пишет их вместе с шагами.
    rows = _rows(db, results[number - 1]) if number <= len(results) else None
    return text + (f"; строк после шага: {rows}" if rows is not None else "")


def _preview(db: DuckDBManager | None, table: str) -> str:
    rows = _rows(db, table)
    if rows is None:
        return "   Результат: таблица результата больше недоступна"
    records = db.to_records(table, limit=PREVIEW_ROWS)
    head = f"   Результат ({rows} строк, показаны первые {len(records)}):"
    return head + "\n" + "\n".join(f"     {line}" for line in format_rows(records).splitlines())


def _rows(db: DuckDBManager | None, table: str) -> int | None:
    """Число строк таблицы; таблицы уже нет (или нет базы) — None."""
    if db is None:
        return None
    try:
        return db.query(f"SELECT COUNT(*) AS n FROM {identifier(table)}")[0]["n"]
    except duckdb.Error:
        return None
