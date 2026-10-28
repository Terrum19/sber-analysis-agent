"""Генерация финального вывода (инсайта) на бизнес-языке.

Требование UX: ответ читается без пояснений автора — «узнали то,
узнали сё, узнать невозможно» (см. prompts/insight_summary.py).
"""

import asyncio
import json
import logging
from decimal import Decimal

from app.config import settings
from app.core.agent.quality_fixes import (
    format_rows,
    format_value,
    insight_facts,
    ungrounded_numbers,
)
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.profiles import ModelPool
from app.core.llm.prompts.insight_summary import INSIGHT_SUMMARY, TILE_INSIGHT
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
    ) -> Insight:
        """Сформировать Insight по финальной таблице.

        supporting — (purpose, таблица) результатов стат-тестов и других
        analysis-шагов, которые дополняют основную таблицу. steps —
        выполненные шаги плана: без них «41» в одной строке не знает, что
        это декабрь и сумма больше 150.
        """
        from app.core.data.duckdb_manager import DuckDBManager

        db = self.db or DuckDBManager()
        result = await db.to_records_async(result_table, limit=SHOWN_ROWS)
        supporting = [
            (purpose, await db.to_records_async(table, limit=10))
            for purpose, table in supporting or []
        ]
        if not settings.agent_quality_fixes:
            extra = "\n".join(f"- {purpose}: {rows}" for purpose, rows in supporting)
            prompt = INSIGHT_SUMMARY.format(
                question=question, result=result, supporting=extra or "нет"
            )
            return await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
        # Строки — компактной таблицей с ISO-датами: repr datetime мешает модели читать ряд.
        shown = format_rows(result)
        extra = "\n".join(f"- {purpose}:\n{format_rows(rows)}" for purpose, rows in supporting)
        prompt = INSIGHT_SUMMARY.format(question=question, result=shown, supporting=extra or "нет")
        try:
            facts = await asyncio.to_thread(insight_facts, db, result_table, SHOWN_ROWS)
        except Exception:
            # Факты — подсказка, а не условие ответа: без них инсайт строится как раньше.
            logger.warning("инсайт: факты по %s не посчитались", result_table, exc_info=True)
            facts = "нет"
        # Одна строка — ответ числом; ряд — лидер, минимум или число строк, а не одно значение.
        # Число строк всей таблицы, а не показанных модели (их не больше SHOWN_ROWS).
        rows = (await db.query_async(f"SELECT COUNT(*) AS n FROM {identifier(result_table)}"))[0]["n"]
        shape = SINGLE_ROW if rows <= 1 else MANY_ROWS.format(rows=rows)
        plan = describe_steps(steps or [])
        prompt += INSIGHT_RULES.format(plan=plan, facts=facts, shape=shape)
        # Числа условий из шагов плана («> 150», «2024») — тоже опора, не выдумка.
        return await self._grounded(prompt, f"{question}\n{shown}\n{extra}\n{facts}\n{plan}")

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
                logger.warning("запасной инсайт: таблица %s не прочитана", result_table, exc_info=True)
        if not records:
            return Insight(
                headline="Не удалось завершить анализ в отведённое время",
                details=[],
                limits=error or "Модель не ответила в пределах бюджета хода",
            )
        values = ", ".join(
            f"{key}: {format_value(value)}" for key, value in records[0].items()
        )
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
