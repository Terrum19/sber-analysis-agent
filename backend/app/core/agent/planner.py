"""Планировщик: вопрос пользователя -> ResearchPlan (список шагов).

Отдаёт LLM вопрос, топ-K релевантных колонок и список тулов.
Результат валидируется по ResearchPlan (см. models/research_step.py).
"""

import logging
from app.core.llm.client import LLMClient

logger = logging.getLogger(__name__)
from app.core.llm.prompts.step_planner import STEP_PLANNER
from app.core.tools.registry import tool_registry
from app.models.research_step import ResearchPlan


class StepPlanner:
    """Планировщик исследования на базе LLM."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        """Сгенерировать план шагов под вопрос."""
        from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST

        tools = tool_registry.describe()
        prompt = STEP_PLANNER.format(
            tools=tools,
            columns=context.get("columns", []),
            context=context.get("summary", ""),
            question=question,
        )
        logger.info(">>> StepPlanner.plan() вопрос=%r", question)
        logger.debug("    промпт=%r", prompt)
        plan = await self.llm.complete_structured(SYSTEM_ANALYST, prompt, ResearchPlan)
        logger.info("<<< StepPlanner.plan() шагов=%d", len(plan.steps))
        # LLM может не включать step_id в шаги — присваиваем последовательные ID.
        for i, step in enumerate(plan.steps):
            step.step_id = i
        return plan
