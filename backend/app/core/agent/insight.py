"""Генерация финального вывода (инсайта) на бизнес-языке.

Требование UX: ответ читается без пояснений автора — «узнали то,
узнали сё, узнать невозможно» (см. prompts/insight_summary.py).
"""

import json
import logging

from app.config import settings
from app.core.agent.quality_fixes import format_rows, insight_facts, ungrounded_numbers
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.prompts.insight_summary import (
    INSIGHT_MEMORIES,
    INSIGHT_SUMMARY,
    INSIGHT_SUPPORTING,
    P_VALUE_RULE,
    TILE_INSIGHT,
)
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
    ) -> None:
        self.llm = llm or LLMClient()
        self.db = db

    async def generate(
        self,
        question: str,
        result_table: str,
        supporting: list[tuple[str, str]] | None = None,
        steps: list[ResearchStep] | None = None,
        memories: list[str] | None = None,
        source: str | None = None,
        inputs: dict[int, str] | None = None,
    ) -> Insight:
        """Сформировать Insight по финальной таблице.

        supporting — (purpose, таблица) результатов стат-тестов и других
        analysis-шагов, которые дополняют основную таблицу. steps —
        выполненные шаги плана: без них «41» в одной строке не знает, что
        это декабрь и сумма больше 150. memories — воспоминания сессии
        («выручка — без НДС»): все включённые, применимость решает модель.
        source — исходная таблица сессии, inputs — шаг -> его входная
        таблица: пропуски и отсечённое лимитом видны только в них.
        """
        from app.core.data.duckdb_manager import DuckDBManager

        db = self.db or DuckDBManager()
        result = db.to_records(result_table, limit=SHOWN_ROWS)
        supporting = [
            (purpose, db.to_records(table, limit=10)) for purpose, table in supporting or []
        ]
        notes = describe_memories(memories or [])
        tested = has_p_value([result, *(rows for _, rows in supporting)])
        if not settings.agent_quality_fixes:
            extra = "\n".join(f"- {purpose}: {rows}" for purpose, rows in supporting)
            prompt = INSIGHT_SUMMARY.format(question=question, result=result)
            prompt += describe_supporting(extra, tested)
            return await self.llm.complete_structured("Ты — аналитик.", prompt + notes, Insight)
        # Строки — компактной таблицей с ISO-датами: repr datetime мешает модели читать ряд.
        shown = format_rows(result)
        extra = "\n".join(f"- {purpose}:\n{format_rows(rows)}" for purpose, rows in supporting)
        prompt = INSIGHT_SUMMARY.format(question=question, result=shown)
        prompt += describe_supporting(extra, tested)
        prompt += notes
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
        prompt += INSIGHT_RULES.format(plan=plan, facts=facts, shape=shape)
        # Опора — весь промпт: условия шагов плана («> 150», «2024»), воспоминания, порог 0.05.
        return await self._grounded(prompt)

    async def generate_tile_insight(self, title: str, result_table: str, chart_type: str) -> str:
        from app.core.data.duckdb_manager import DuckDBManager

        db = self.db or DuckDBManager()
        result = db.to_records(result_table, limit=TILE_ROWS)
        prompt = TILE_INSIGHT.format(title=title, chart_type=chart_type, result=result)
        text = await self.llm.complete("Ты — аналитик.", prompt)
        return text.strip()

    async def _grounded(self, prompt: str) -> Insight:
        """H-Q3: число в headline, которого нет в промпте, — один переспрос."""
        insight = await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
        missing = ungrounded_numbers(insight.headline, prompt)
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
