"""Генерация финального вывода (инсайта) на бизнес-языке.

Требование UX: ответ читается без пояснений автора — «узнали то,
узнали сё, узнать невозможно» (см. prompts/insight_summary.py).
"""

from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.prompts.insight_summary import INSIGHT_SUMMARY
from app.models.insight import Insight


class InsightGenerator:
    """Генератор итогового вывода по результату исследования."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        db: DuckDBManager | None = None,
    ) -> None:
        self.llm = llm or LLMClient()
        self.db = db

    async def generate(self, question: str, result_table: str) -> Insight:
        """Сформировать Insight по финальной таблице."""
        from app.core.data.duckdb_manager import DuckDBManager

        db = self.db or DuckDBManager()
        result = db.to_records(result_table, limit=50)
        prompt = INSIGHT_SUMMARY.format(question=question, result=result)
        return await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
