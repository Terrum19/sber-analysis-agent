"""Генерация финального вывода (инсайта) на бизнес-языке.

Требование UX: ответ читается без пояснений автора — «узнали то,
узнали сё, узнать невозможно» (см. prompts/insight_summary.py).
"""

import logging

from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.prompts.insight_summary import INSIGHT_SUMMARY
from app.models.insight import Insight

logger = logging.getLogger(__name__)


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
        result = db.to_records(result_table, limit=50)
        extra = "\n".join(
            f"- {purpose}: {db.to_records(table, limit=10)}" for purpose, table in supporting or []
        )
        prompt = INSIGHT_SUMMARY.format(question=question, result=result, supporting=extra or "нет")
        from app.config import settings

        if settings.agent_quality_fixes:
            from app.core.agent.quality_fixes import INSIGHT_RULES, insight_facts

            prompt += INSIGHT_RULES.format(facts=insight_facts(db, result_table, shown=50))
            return await self._grounded(prompt)
        return await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)

    async def _grounded(self, prompt: str) -> Insight:
        """H-Q3: число в headline, которого нет в фактах, — один переспрос."""
        from app.core.agent.quality_fixes import NUMBER_FIX, ungrounded_numbers

        insight = await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
        missing = ungrounded_numbers(insight.headline, prompt)
        if not missing:
            return insight
        logger.warning("инсайт: числа без опоры в фактах %s, переспрос", missing)
        fix = NUMBER_FIX.format(numbers=", ".join(missing))
        return await self.llm.complete_structured("Ты — аналитик.", prompt + fix, Insight)
