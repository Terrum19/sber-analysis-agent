"""Генерация финального вывода (инсайта) на бизнес-языке.

Требование UX: ответ читается без пояснений автора — «узнали то,
узнали сё, узнать невозможно» (см. prompts/insight_summary.py).
"""

import logging

from app.config import settings
from app.core.agent.quality_fixes import format_rows, insight_facts, ungrounded_numbers
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.prompts.insight_summary import INSIGHT_SUMMARY
from app.core.llm.prompts.quality_rules import (
    INSIGHT_RULES,
    MANY_ROWS,
    NUMBER_FIX,
    SINGLE_ROW,
)
from app.models.insight import Insight

logger = logging.getLogger(__name__)

# Сколько строк результата видит модель.
SHOWN_ROWS = 50


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
    ) -> Insight:
        """Сформировать Insight по финальной таблице.

        supporting — (purpose, таблица) результатов стат-тестов и других
        analysis-шагов, которые дополняют основную таблицу.
        """
        from app.core.data.duckdb_manager import DuckDBManager

        db = self.db or DuckDBManager()
        result = db.to_records(result_table, limit=SHOWN_ROWS)
        supporting = [
            (purpose, db.to_records(table, limit=10)) for purpose, table in supporting or []
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
            facts = insight_facts(db, result_table, shown=SHOWN_ROWS)
        except Exception:
            # Факты — подсказка, а не условие ответа: без них инсайт строится как раньше.
            logger.warning("инсайт: факты по %s не посчитались", result_table, exc_info=True)
            facts = "нет"
        # Одна строка — ответ числом; ряд — лидер, минимум или число строк, а не одно значение.
        shape = SINGLE_ROW if len(result) <= 1 else MANY_ROWS.format(rows=len(result))
        prompt += INSIGHT_RULES.format(facts=facts, shape=shape)
        return await self._grounded(prompt, f"{question}\n{shown}\n{extra}\n{facts}")

    async def _grounded(self, prompt: str, sources: str) -> Insight:
        """H-Q3: число в headline, которого нет в sources, — один переспрос."""
        insight = await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
        missing = ungrounded_numbers(insight.headline, sources)
        if not missing:
            return insight
        logger.warning("инсайт: числа без опоры в фактах %s, переспрос", missing)
        fix = NUMBER_FIX.format(numbers=", ".join(missing))
        return await self.llm.complete_structured("Ты — аналитик.", prompt + fix, Insight)
