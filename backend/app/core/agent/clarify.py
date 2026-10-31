"""Уточняющие вопросы глубокого исследования (A-28): до начала хода.

Один вызов LLM по вопросу, описанию таблицы, последним ходам, воспоминаниям
и ходу из reply_to. Вопрос однозначен — вопросов нет, фронт сразу запускает
ход. Иначе 3–4 вопроса с вариантами; варианты, которые ссылаются на колонки,
которых нет в таблице и связанных с ней, отбрасываются, как и вопросы, у
которых осталось меньше двух вариантов.

Невалидный ответ (сбой модели или меньше трёх годных вопросов) — повтор
вызова с причиной отказа в промпте, до ATTEMPTS попыток. После последней
хватает и двух годных вопросов из лучшей попытки; меньше — ClarifyFailed.

Реплика без исследования (привет, обзор таблицы, смена вида графика) —
без вызова LLM: вопросов нет.
"""

import logging
from collections.abc import Sequence

import duckdb

from app.core.agent.triage import small_talk_reply
from app.core.dashboards.ideas import describe_table
from app.core.data import overview, relations
from app.core.llm.client import LLMClient
from app.core.llm.prompts.clarify import (
    CLARIFY_MEMORIES,
    CLARIFY_QUESTIONS,
    CLARIFY_REPLY,
    CLARIFY_RETRY,
    CLARIFY_TURNS,
)
from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST
from app.core.memory.profile_memory import ProfileMemory
from app.core.memory.session_memory import SessionMemory
from app.core.viz.chart_morph import detect_morph
from app.models.clarify import (
    MAX_OPTIONS,
    MAX_QUESTIONS,
    MIN_KEPT_QUESTIONS,
    MIN_OPTIONS,
    MIN_QUESTIONS,
    ClarifyAnswer,
    ClarifyQuestion,
    describe_clarifications,
)

logger = logging.getLogger(__name__)

# Вызовов LLM на один запрос вопросов.
ATTEMPTS = 3
# Текст ошибки модели в промпте повтора.
REASON_CHARS = 300
# Текст вопроса и варианта в причине отказа: модель, «поплывшая» в text,
# не получает свой мусор обратно в промпт повтора.
QUOTE_CHARS = 60


class ClarifyFailed(Exception):
    """Модель не дала годных вопросов и после всех попыток."""


async def clarify_questions(
    db,
    memory: SessionMemory,
    question: str,
    reply_to: str | None = None,
    llm: LLMClient | None = None,
) -> list[ClarifyQuestion]:
    """Вопросы к пользователю до начала хода; пустой список — вопроса хватает."""
    if not needs_research(question):
        logger.info("уточнения не нужны: реплика без исследования")
        return []
    prompt = _prompt(db, memory, question, reply_to)
    known = known_columns(db, memory.table)
    llm = llm or LLMClient()
    best: list[ClarifyQuestion] = []
    reasons: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        retry = (
            CLARIFY_RETRY.format(reasons="\n".join(f"- {r}" for r in reasons)) if reasons else ""
        )
        try:
            answer = await llm.complete_structured(SYSTEM_ANALYST, prompt + retry, ClarifyAnswer)
        except Exception as exc:  # noqa: BLE001 — сбой модели: повтор с причиной
            logger.warning("уточнения, попытка %d: модель не ответила: %s", attempt, exc)
            reasons = [f"ответ не разобран: {str(exc)[:REASON_CHARS]}"]
            continue
        if answer.enough_info:
            logger.info("уточнения: вопроса хватает")
            return []
        questions, reasons = checked(answer.questions, known)
        if len(questions) >= MIN_QUESTIONS:
            logger.info("уточнения: %s", [item.text for item in questions])
            return questions
        best = max(best, questions, key=len)
        reasons.append(f"годных вопросов {len(questions)}, а нужно {MIN_QUESTIONS}–{MAX_QUESTIONS}")
        logger.warning("уточнения, попытка %d: %s", attempt, "; ".join(reasons))
    if len(best) >= MIN_KEPT_QUESTIONS:
        logger.info("уточнения после всех попыток: %s", [item.text for item in best])
        return best
    raise ClarifyFailed("; ".join(reasons) or "модель не задала вопросов")


def needs_research(question: str) -> bool:
    """Вопрос ведёт к исследованию: не small talk, не обзор таблицы, не смена вида графика."""
    return (
        small_talk_reply(question) is None
        and not overview.is_overview_question(question)
        and detect_morph(question) is None
    )


def checked(
    questions: Sequence[ClarifyQuestion], known: set[str]
) -> tuple[list[ClarifyQuestion], list[str]]:
    """Годные вопросы и причины отказа для промпта повтора.

    Вариант с колонкой не из known отбрасывается, повтор варианта — тоже;
    вопрос, у которого осталось меньше MIN_OPTIONS вариантов, и второй вопрос
    на ту же тему — тоже.
    """
    kept: list[ClarifyQuestion] = []
    reasons: list[str] = []
    for item in questions:
        text = item.text.strip()
        options = []
        seen: set[str] = set()
        for option in item.options:
            label = option.text.strip()
            unknown = [
                column for column in option.columns if column.strip().casefold() not in known
            ]
            if unknown:
                reasons.append(
                    f"вариант «{_quote(label)}» ссылается на колонки, которых нет: "
                    f"{', '.join(_quote(column) for column in unknown)}"
                )
                continue
            if label and label.casefold() not in seen:
                seen.add(label.casefold())
                options.append(option.model_copy(update={"text": label}))
        if not text or len(options) < MIN_OPTIONS:
            reasons.append(f"у вопроса «{_quote(text)}» меньше {MIN_OPTIONS} вариантов из данных")
            continue
        if any(other.axis == item.axis for other in kept):
            reasons.append(f"второй вопрос на тему {item.axis}")
            continue
        kept.append(item.model_copy(update={"text": text, "options": options[:MAX_OPTIONS]}))
    return kept[:MAX_QUESTIONS], reasons


def _quote(text: str) -> str:
    """Текст для причины отказа: не длиннее QUOTE_CHARS."""
    return text if len(text) <= QUOTE_CHARS else f"{text[:QUOTE_CHARS].rstrip()}…"


def known_columns(db, table: str) -> set[str]:
    """Имена и названия колонок таблицы и связанных с ней, в нижнем регистре."""
    try:
        tables = relations.component(relations.load(db), table) | {table}
    except duckdb.Error:
        logger.exception("связанные таблицы %s не прочитаны", table)
        tables = {table}
    passport = ProfileMemory(db)
    names: set[str] = set()
    for name in tables:
        names.update(db.columns(name))
        names.update(row.title for row in passport.load_enrichment(name) if row.title)
    return {name.strip().casefold() for name in names}


def describe_turn(turn: dict) -> str:
    """Прошлый ход для промпта: вопрос, ответ и уточнения."""
    number = f"Ход {turn['number']}. " if turn.get("number") else ""
    lines = [f"{number}Вопрос: {turn['question']}"]
    if turn.get("headline"):
        lines.append(f"   Ответ: {turn['headline']}")
    if turn.get("clarifications"):
        lines.append(f"   Уточнения: {describe_clarifications(turn['clarifications'])}")
    return "\n".join(lines)


def _prompt(db, memory: SessionMemory, question: str, reply_to: str | None) -> str:
    target = memory.answered_turn(reply_to) if reply_to else None
    turns = memory.recent_turns()
    memories = memory.context_memories()
    return CLARIFY_QUESTIONS.format(
        question=question,
        reply=CLARIFY_REPLY.format(turn=describe_turn(target)) if target else "",
        table=describe_table(db, memory.table, question),
        turns=(
            CLARIFY_TURNS.format(turns="\n".join(describe_turn(turn) for turn in turns))
            if turns
            else ""
        ),
        memories=(
            CLARIFY_MEMORIES.format(memories="\n".join(f"- {text}" for text in memories))
            if memories
            else ""
        ),
        min_questions=MIN_QUESTIONS,
        max_questions=MAX_QUESTIONS,
        min_options=MIN_OPTIONS,
        max_options=MAX_OPTIONS,
    )
