"""Генерация финального вывода (инсайта) на бизнес-языке.

Требование UX: ответ читается без пояснений автора — «узнали то,
узнали сё, узнать невозможно» (см. prompts/insight_summary.py).
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from decimal import Decimal
from typing import NamedTuple

from app.config import settings
from app.core.agent.quality_fixes import (
    format_rows,
    format_value,
    insight_facts,
    period_number_facts,
    plan_facts,
    ungrounded_numbers,
)
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.profiles import ModelPool
from app.core.llm.prompts.insight_summary import (
    INSIGHT_MEMORIES,
    INSIGHT_STREAM,
    INSIGHT_SUMMARY,
    INSIGHT_SUPPORTING,
    P_VALUE_RULE,
    TILE_INSIGHT,
)
from app.core.llm.prompts.quality_rules import (
    INSIGHT_RULES,
    MANY_ROWS,
    NO_ANSWER_FIX,
    NUMBER_FIX,
    RESULT_SHAPES,
    SINGLE_ROW,
)
from app.core.memory.profile_memory import ProfileMemory
from app.core.tools.base import identifier
from app.models.insight import Insight
from app.models.research_step import ResearchStep

logger = logging.getLogger(__name__)

# Сколько строк результата видит модель.
SHOWN_ROWS = 50
TILE_ROWS = 30

# «Ограничения:», «Ограничение:», «Лимиты:» — в т.ч. после bullet-маркера.
_LIMITS_RE = re.compile(r"^(?:ограничени[ея]|лимиты)\s*[:\-—]\s*(.*)$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^(?:[-–—*•]|\d+[.)])\s+(.*)$")
_HEADLINE_LABEL_RE = re.compile(r"^(?:headline|вывод)\s*:\s*", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


class _Materials(NamedTuple):
    """Всё, что уходит в промпт инсайта и в проверку чисел."""

    shown: str
    supporting: str
    facts: str
    plan: str
    shape: str
    sources: str
    hints: str = ""
    notes: str = ""
    tested: bool = False


def _ignore_delta(delta: str) -> None:
    """emit по умолчанию: дельты некому отдавать."""


def _clean_line(line: str) -> str:
    """Убрать markdown-разметку строки: заголовки, **, __, backticks."""
    line = line.strip()
    line = re.sub(r"^#+\s*", "", line)
    return line.replace("**", "").replace("__", "").replace("`", "").strip()


def parse_insight_text(text: str) -> Insight:
    """Разобрать текстовый ответ стрима в Insight.

    Формат нестрогий: headline — первая содержательная строка, details —
    строки с bullet-маркерами (без маркеров — каждая непустая строка),
    limits — всё после «Ограничения:». Markdown снимается.
    """
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())

    headline = ""
    details: list[str] = []
    limits: list[str] = []
    in_limits = False
    last_was_bullet = False

    for raw in text.replace("\r\n", "\n").split("\n"):
        line = _clean_line(raw)
        if not line:
            continue

        bullet = _BULLET_RE.match(line)
        content = bullet.group(1).strip() if bullet else line

        if in_limits:
            limits.append(content)
            continue

        limit = _LIMITS_RE.match(content)
        if limit:
            in_limits = True
            if limit.group(1).strip():
                limits.append(limit.group(1).strip())
            continue

        if bullet:
            details.append(_clean_line(content))
            last_was_bullet = True
        elif not headline:
            headline = _HEADLINE_LABEL_RE.sub("", content).strip()
            last_was_bullet = False
        elif details and last_was_bullet:
            details[-1] = f"{details[-1]} {content}".strip()
        else:
            details.append(content)
            last_was_bullet = False

    return Insight(headline=headline, details=details, limits=" ".join(limits).strip())


def _first_sentence(text: str) -> str:
    """Первое предложение первой содержательной строки — fallback headline."""
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = _clean_line(raw)
        if not line:
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            line = bullet.group(1).strip()
        line = _HEADLINE_LABEL_RE.sub("", line)
        limit = _LIMITS_RE.match(line)
        if limit:
            line = limit.group(1).strip() or line
        return _SENTENCE_RE.split(line, maxsplit=1)[0].strip()
    return ""


class InsightGenerator:
    """Генератор итогового вывода по результату исследования."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        db: DuckDBManager | None = None,
        pool: ModelPool | None = None,
    ) -> None:
        self.llm = llm or (pool.get("planner") if pool is not None else LLMClient())
        self.fast_llm = pool.get("fast") if pool is not None else self.llm
        self.db = db

    async def generate(
        self,
        question: str,
        result_table: str,
        supporting: list[tuple[str, str]] | None = None,
        steps: list[ResearchStep] | None = None,
        memories: list[str] | None = None,
        result_tool: str = "",
        source: str | None = None,
        inputs: dict[int, str] | None = None,
    ) -> Insight:
        """Сформировать Insight по финальной таблице.

        supporting — (purpose, таблица) результатов стат-тестов и других
        analysis-шагов, которые дополняют основную таблицу. steps —
        выполненные шаги плана: без них «41» в одной строке не знает, что
        это декабрь и сумма больше 150. memories — воспоминания сессии
        («выручка — без НДС»): все включённые, применимость решает модель.
        result_tool — тул шага с результатом: у тренда и сезонности своё
        правило headline (RESULT_SHAPES). source — исходная таблица сессии,
        inputs — шаг -> его входная таблица: пропуски и отсечённое лимитом
        видны только в них.
        """
        materials = await self._materials(
            question,
            result_table,
            supporting,
            steps,
            memories=memories,
            result_tool=result_tool,
            source=source,
            inputs=inputs,
        )
        if not settings.agent_quality_fixes:
            prompt = INSIGHT_SUMMARY.format(question=question, result=materials.shown)
            prompt += describe_supporting(materials.supporting, materials.tested)
            return await self.llm.complete_structured(
                "Ты — аналитик.", prompt + materials.notes, Insight
            )
        prompt = INSIGHT_SUMMARY.format(question=question, result=materials.shown)
        prompt += describe_supporting(materials.supporting, materials.tested)
        prompt += materials.notes
        prompt += INSIGHT_RULES.format(
            plan=materials.plan, facts=materials.facts, shape=materials.shape, hints=materials.hints
        )
        return await self._grounded(prompt, needs_number=bool(re.search(r"\d", materials.shown)))

    async def stream(
        self,
        question: str,
        result_table: str,
        supporting: list[tuple[str, str]] | None = None,
        steps: list[ResearchStep] | None = None,
        memories: list[str] | None = None,
        result_tool: str = "",
        source: str | None = None,
        inputs: dict[int, str] | None = None,
        emit: Callable[[str], None] | None = None,
    ) -> Insight:
        """Стримить дельты текста через emit и вернуть разобранный Insight.

        Нет complete_stream (тестовые двойники) или ошибка до первой дельты —
        structured-фолбэк через generate(). Ошибка после первой дельты не
        фолбэчится: текст уже показан, второй ответ дал бы дубль.
        """
        emit = emit or _ignore_delta
        complete_stream = getattr(self.llm, "complete_stream", None)
        if not callable(complete_stream):
            return await self.generate(
                question,
                result_table,
                supporting=supporting,
                steps=steps,
                memories=memories,
                result_tool=result_tool,
                source=source,
                inputs=inputs,
            )

        materials = await self._materials(
            question,
            result_table,
            supporting,
            steps,
            memories=memories,
            result_tool=result_tool,
            source=source,
            inputs=inputs,
        )
        prompt = INSIGHT_STREAM.format(
            question=question, result=materials.shown, supporting=materials.supporting or "нет"
        )
        if settings.agent_quality_fixes:
            prompt += INSIGHT_RULES.format(
                plan=materials.plan,
                facts=materials.facts,
                shape=materials.shape,
                hints=materials.hints,
            )

        chunks: list[str] = []
        try:
            async for delta in complete_stream("Ты — аналитик.", prompt):
                chunks.append(delta)
                emit(delta)
        except Exception:
            if chunks:
                logger.exception("инсайт: стрим оборвался после %d дельт", len(chunks))
                raise
            logger.warning("инсайт: стрим не начался, structured fallback", exc_info=True)
            return await self.generate(
                question,
                result_table,
                supporting=supporting,
                steps=steps,
                memories=memories,
                result_tool=result_tool,
                source=source,
                inputs=inputs,
            )

        text = "".join(chunks)
        insight = parse_insight_text(text)
        if not insight.headline:
            insight.headline = _first_sentence(text)
        if not insight.headline and text.strip():
            # Совсем без headline, но текст уже отдан: не дублируем его generate().
            insight.headline = text.strip().splitlines()[0][:200]
        if not insight.headline:
            logger.warning("инсайт: стрим пуст, structured fallback")
            return await self.generate(
                question,
                result_table,
                supporting=supporting,
                steps=steps,
                memories=memories,
                result_tool=result_tool,
                source=source,
                inputs=inputs,
            )

        if settings.agent_quality_fixes:
            missing = ungrounded_numbers(insight.headline, materials.sources)
            if missing:
                logger.warning(
                    "инсайт (стрим): числа без опоры %s; текст уже отдан, переспроса нет",
                    missing,
                )
        logger.info(
            "инсайт (стрим): дельт=%d символов=%d headline=%r",
            len(chunks),
            len(text),
            insight.headline,
        )
        return insight

    async def _materials(
        self,
        question: str,
        result_table: str,
        supporting: list[tuple[str, str]] | None = None,
        steps: list[ResearchStep] | None = None,
        memories: list[str] | None = None,
        result_tool: str = "",
        source: str | None = None,
        inputs: dict[int, str] | None = None,
    ) -> _Materials:
        """Общая подготовка промпта для generate() и stream().

        При выключенных quality fixes — ровно прежний промпт (repr строк,
        без фактов, формы и шагов). Иначе — компактная таблица, факты,
        форма результата и шаги плана.
        """
        from app.core.data.duckdb_manager import DuckDBManager

        db = self.db or DuckDBManager()
        result = await db.to_records_async(result_table, limit=SHOWN_ROWS)
        supporting_records = [
            (purpose, await db.to_records_async(table, limit=10))
            for purpose, table in supporting or []
        ]
        notes = describe_memories(memories or [])
        tested = has_p_value([result, *(rows for _, rows in supporting_records)])
        extra = "\n".join(
            f"- {purpose}:\n{format_rows(rows)}" for purpose, rows in supporting_records
        )
        if not settings.agent_quality_fixes:
            extra_plain = "\n".join(f"- {purpose}: {rows}" for purpose, rows in supporting_records)
            return _Materials(
                shown=str(result),
                supporting=extra_plain,
                facts="нет",
                plan="нет",
                shape="",
                sources="",
                hints="",
                notes=notes,
                tested=tested,
            )
        # Строки — компактной таблицей с ISO-датами: repr datetime мешает модели читать ряд.
        shown = format_rows(result)
        try:
            facts = await asyncio.to_thread(insight_facts, db, result_table, SHOWN_ROWS)
        except Exception:
            logger.warning("инсайт: факты по %s не посчитались", result_table, exc_info=True)
            facts = ""
        hints: list[str] = []
        try:
            profile = ProfileMemory(db).load_profile(source) if source else None
            columns = list(result[0]) if result else []
            more, hints = plan_facts(db, steps or [], inputs or {}, profile, columns)
        except Exception:
            logger.warning("инсайт: факты по плану не посчитались", exc_info=True)
            more, hints = [], []
        more += period_number_facts(steps or [])
        facts_str = "\n".join(line for line in [facts, *more] if line) or "нет"
        # Одна строка — ответ числом; ряд — лидер, минимум или число строк, а не одно значение.
        # Число строк всей таблицы, а не показанных модели (их не больше SHOWN_ROWS).
        rows = (await db.query_async(f"SELECT COUNT(*) AS n FROM {identifier(result_table)}"))[0][
            "n"
        ]
        shape = SINGLE_ROW if rows <= 1 else MANY_ROWS.format(rows=rows)
        shape = RESULT_SHAPES.get(result_tool, shape)
        plan = describe_steps(steps or [])
        hints_str = "".join(hints)
        sources = f"{question}\n{shown}\n{extra}\n{facts_str}\n{plan}"
        return _Materials(
            shown=shown,
            supporting=extra,
            facts=facts_str,
            plan=plan,
            shape=shape,
            sources=sources,
            hints=hints_str,
            notes=notes,
            tested=tested,
        )

    def fast_insight(
        self, question: str, result_table: str, steps: list[ResearchStep] | None = None
    ) -> Insight | None:
        """Инсайт без LLM, если результат — одна строка и одна числовая ячейка.

        Такой вопрос («сколько строк», «общая сумма») не требует бизнес-текста:
        число из SQL и есть ответ. Любая другая форма уходит в LLM.
        """
        if self.db is None:
            return None
        try:
            records = self.db.to_records(result_table, limit=2)
        except Exception:
            logger.warning("быстрый инсайт: таблица %s не прочитана", result_table, exc_info=True)
            return None
        if len(records) != 1 or len(records[0]) != 1:
            return None
        value = next(iter(records[0].values()))
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)) or value is None:
            return None
        text = format_value(value)
        headline = f"{question.strip()} — {text}".strip()
        return Insight(
            headline=headline[:300],
            details=[f"Строк в результате: {len(records)}", f"Колонка: {next(iter(records[0]))}"],
            limits="",
        )

    def fallback_insight(
        self,
        question: str,
        result_table: str,
        steps: list[ResearchStep] | None = None,
        error: str = "",
    ) -> Insight:
        """Шаблонный инсайт, когда LLM/бюджет не позволили построить обычный."""
        records: list[dict] = []
        if self.db is not None and result_table:
            try:
                records = self.db.to_records(result_table, limit=5)
            except Exception:
                logger.warning(
                    "запасной инсайт: таблица %s не прочитана", result_table, exc_info=True
                )
        if not records:
            return Insight(
                headline="Не удалось завершить анализ в отведённое время",
                details=[],
                limits=error or "Модель не ответила в пределах бюджета хода",
            )
        values = ", ".join(f"{key}: {format_value(value)}" for key, value in records[0].items())
        return Insight(
            headline="Результат готов",
            details=[f"Строк в результате: {len(records)}", values[:300]],
            limits=error,
        )

    async def generate_tile_insight(self, title: str, result_table: str, chart_type: str) -> str:
        from app.core.data.duckdb_manager import DuckDBManager

        db = self.db or DuckDBManager()
        result = await db.to_records_async(result_table, limit=TILE_ROWS)
        if len(result) == 1 and len(result[0]) == 1:
            value = next(iter(result[0].values()))
            if not isinstance(value, bool) and isinstance(value, (int, float, Decimal)):
                return f"{title}: {format_value(value)}"
        prompt = TILE_INSIGHT.format(title=title, chart_type=chart_type, result=result)
        text = await self.fast_llm.complete("Ты — аналитик.", prompt)
        return text.strip()

    async def _grounded(self, prompt: str, needs_number: bool = True) -> Insight:
        """Один переспрос, если в headline нет ответа числом или число без опоры (H-Q3).

        Без числа модель пишет «что посчитано», но не сам ответ («Средняя сумма
        события»); проверка в коде надёжнее ещё одного правила в промпте.
        needs_number=False — в результате нет чисел, ответ словами допустим.
        """
        insight = await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
        if needs_number and not re.search(r"\d", insight.headline):
            logger.warning("инсайт: в headline нет числа, переспрос")
            fix = NO_ANSWER_FIX
        elif missing := ungrounded_numbers(insight.headline, prompt):
            logger.warning("инсайт: числа без опоры в фактах %s, переспрос", missing)
            fix = NUMBER_FIX.format(numbers=", ".join(missing))
        else:
            return insight
        return await self.llm.complete_structured("Ты — аналитик.", prompt + fix, Insight)


def describe_steps(steps: list[ResearchStep]) -> str:
    """Шаги плана для вывода: «1. purpose — tool {аргументы}»."""
    lines = [
        f"{number}. {step.purpose or step.tool} — {step.tool} "
        f"{json.dumps(step.arguments or step.columns, ensure_ascii=False, default=str)}"
        for number, step in enumerate(steps, 1)
    ]
    return "\n".join(lines) or "нет"


def has_p_value(tables: list[list[dict]]) -> bool:
    """Есть ли колонка p_value хотя бы в одной таблице: только тогда нужен порог 0.05."""
    return any("p_value" in (key.lower() for key in rows[0]) for rows in tables if rows)


def describe_supporting(extra: str, tested: bool) -> str:
    """Сопутствующие результаты — когда они есть, порог p_value — когда есть тест."""
    text = INSIGHT_SUPPORTING.format(supporting=extra) if extra else ""
    return text + (P_VALUE_RULE if tested else "")


def describe_memories(memories: list[str]) -> str:
    """Блок воспоминаний для вывода; без воспоминаний — пусто."""
    if not memories:
        return ""
    return INSIGHT_MEMORIES.format(memories="\n".join(f"- {text}" for text in memories))
