"""Генерация финального вывода (инсайта) на бизнес-языке.

Требование UX: ответ читается без пояснений автора — «узнали то,
узнали сё, узнать невозможно» (см. prompts/insight_summary.py).
"""

import json
import logging
import re

from app.config import settings
from app.core.agent.quality_fixes import format_rows, insight_facts, ungrounded_numbers
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.prompts.insight_summary import INSIGHT_SUMMARY
from app.core.llm.prompts.quality_rules import (
    INSIGHT_RULES,
    MANY_ROWS,
    NO_ANSWER_FIX,
    NUMBER_FIX,
    SINGLE_ROW,
)
from app.core.tools.base import identifier
from app.models.insight import Insight
from app.models.research_step import ResearchStep

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
        # Число строк всей таблицы, а не показанных модели (их не больше SHOWN_ROWS).
        rows = db.query(f"SELECT COUNT(*) AS n FROM {identifier(result_table)}")[0]["n"]
        shape = SINGLE_ROW if rows <= 1 else MANY_ROWS.format(rows=rows)
        plan = describe_steps(steps or [])
        prompt += INSIGHT_RULES.format(plan=plan, facts=facts, shape=shape)
        # Числа условий из шагов плана («> 150», «2024») — тоже опора, не выдумка.
        return await self._grounded(
            prompt,
            f"{question}\n{shown}\n{extra}\n{facts}\n{plan}",
            needs_number=bool(re.search(r"\d", shown)),
        )

    async def _grounded(self, prompt: str, sources: str, needs_number: bool = True) -> Insight:
        """Один переспрос, если в headline нет ответа числом или число без опоры (H-Q3).

        Без числа модель пишет «что посчитано», но не сам ответ («Средняя сумма
        события»); проверка в коде надёжнее ещё одного правила в промпте.
        needs_number=False — в результате нет чисел, ответ словами допустим.
        """
        insight = await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
        if needs_number and not re.search(r"\d", insight.headline):
            logger.warning("инсайт: в headline нет числа, переспрос")
            fix = NO_ANSWER_FIX
        elif missing := ungrounded_numbers(insight.headline, sources):
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
