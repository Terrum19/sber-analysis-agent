"""Генерация финального вывода (инсайта) на бизнес-языке.

Требование UX: ответ читается без пояснений автора — «узнали то,
узнали сё, узнать невозможно» (см. prompts/insight_summary.py).
"""

import json
import logging
import re
from collections.abc import Callable
from typing import NamedTuple

from app.config import settings
from app.core.agent.quality_fixes import format_rows, insight_facts, ungrounded_numbers
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.prompts.insight_summary import INSIGHT_STREAM, INSIGHT_SUMMARY, TILE_INSIGHT
from app.core.llm.prompts.quality_rules import (
    INSIGHT_RULES,
    MANY_ROWS,
    NUMBER_FIX,
    SINGLE_ROW,
)
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
    ) -> None:
        self.llm = llm or LLMClient()
        self.db = db

    async def generate(
        self,
        question: str,
        result_table: str,
        supporting: list[tuple[str, str]] | None = None,
        steps: list[ResearchStep] | None = None,
    ) -> Insight:
        """Сформировать Insight по финальной таблице.

        supporting — (purpose, таблица) результатов стат-тестов и других
        analysis-шагов, которые дополняют основную таблицу. steps —
        выполненные шаги плана: без них «41» в одной строке не знает, что
        это декабрь и сумма больше 150.
        """
        materials = self._materials(question, result_table, supporting, steps)
        prompt = INSIGHT_SUMMARY.format(
            question=question, result=materials.shown, supporting=materials.supporting
        )
        if not settings.agent_quality_fixes:
            return await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
        prompt += INSIGHT_RULES.format(
            plan=materials.plan, facts=materials.facts, shape=materials.shape
        )
        return await self._grounded(prompt, materials.sources)

    async def stream(
        self,
        question: str,
        result_table: str,
        supporting: list[tuple[str, str]] | None = None,
        steps: list[ResearchStep] | None = None,
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
            return await self.generate(question, result_table, supporting=supporting, steps=steps)

        materials = self._materials(question, result_table, supporting, steps)
        prompt = INSIGHT_STREAM.format(
            question=question, result=materials.shown, supporting=materials.supporting or "нет"
        )
        if settings.agent_quality_fixes:
            prompt += INSIGHT_RULES.format(
                plan=materials.plan, facts=materials.facts, shape=materials.shape
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
            return await self.generate(question, result_table, supporting=supporting, steps=steps)

        text = "".join(chunks)
        insight = parse_insight_text(text)
        if not insight.headline:
            insight.headline = _first_sentence(text)
        if not insight.headline and text.strip():
            # Совсем без headline, но текст уже отдан: не дублируем его generate().
            insight.headline = text.strip().splitlines()[0][:200]
        if not insight.headline:
            logger.warning("инсайт: стрим пуст, structured fallback")
            return await self.generate(question, result_table, supporting=supporting, steps=steps)

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

    def _materials(
        self,
        question: str,
        result_table: str,
        supporting: list[tuple[str, str]] | None,
        steps: list[ResearchStep] | None,
    ) -> _Materials:
        """Общая подготовка промпта для generate() и stream().

        При выключенных quality fixes — ровно прежний промпт (repr строк,
        без фактов, формы и шагов). Иначе — компактная таблица, факты,
        форма результата и шаги плана.
        """
        from app.core.data.duckdb_manager import DuckDBManager

        db = self.db or DuckDBManager()
        result = db.to_records(result_table, limit=SHOWN_ROWS)
        supporting = [
            (purpose, db.to_records(table, limit=10)) for purpose, table in supporting or []
        ]
        if not settings.agent_quality_fixes:
            extra = "\n".join(f"- {purpose}: {rows}" for purpose, rows in supporting)
            return _Materials(
                shown=str(result),
                supporting=extra or "нет",
                facts="нет",
                plan="нет",
                shape="",
                sources="",
            )
        # Строки — компактной таблицей с ISO-датами: repr datetime мешает модели читать ряд.
        shown = format_rows(result)
        extra = "\n".join(f"- {purpose}:\n{format_rows(rows)}" for purpose, rows in supporting)
        try:
            facts = insight_facts(db, result_table, shown=SHOWN_ROWS)
        except Exception:
            # Факты — подсказка, а не условие ответа: без них инсайт строится как раньше.
            logger.warning("инсайт: факты по %s не посчитались", result_table, exc_info=True)
            facts = "нет"
        # Одна строка — ответ числом; ряд — лидер, минимум или число строк, а не одно значение.
        # Число строк всей таблицы, а не показанных модели (их не больше SHOWN_ROWS).
        rows = db.query(f"SELECT COUNT(*) AS n FROM {identifier(result_table)}")[0]["n"]
        shape = SINGLE_ROW if rows <= 1 else MANY_ROWS.format(rows=rows)
        plan = describe_steps(steps or [])
        # Числа условий из шагов плана («> 150», «2024») — тоже опора, не выдумка.
        return _Materials(
            shown=shown,
            supporting=extra or "нет",
            facts=facts,
            plan=plan,
            shape=shape,
            sources=f"{question}\n{shown}\n{extra}\n{facts}\n{plan}",
        )

    async def generate_tile_insight(self, title: str, result_table: str, chart_type: str) -> str:
        from app.core.data.duckdb_manager import DuckDBManager

        db = self.db or DuckDBManager()
        result = db.to_records(result_table, limit=TILE_ROWS)
        prompt = TILE_INSIGHT.format(title=title, chart_type=chart_type, result=result)
        text = await self.llm.complete("Ты — аналитик.", prompt)
        return text.strip()

    async def _grounded(self, prompt: str, sources: str) -> Insight:
        """H-Q3: число в headline, которого нет в sources, — один переспрос."""
        insight = await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
        missing = ungrounded_numbers(insight.headline, sources)
        if not missing:
            return insight
        logger.warning("инсайт: числа без опоры в фактах %s, переспрос", missing)
        fix = NUMBER_FIX.format(numbers=", ".join(missing))
        return await self.llm.complete_structured("Ты — аналитик.", prompt + fix, Insight)


def describe_steps(steps: list[ResearchStep]) -> str:
    """Шаги плана для вывода: «1. purpose — tool {аргументы}»."""
    lines = [
        f"{number}. {step.purpose or step.tool} — {step.tool} "
        f"{json.dumps(step.arguments or step.columns, ensure_ascii=False, default=str)}"
        for number, step in enumerate(steps, 1)
    ]
    return "\n".join(lines) or "нет"
